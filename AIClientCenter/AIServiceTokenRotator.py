import os
import json
import time
import logging
import threading
from typing import Dict, List, Optional
from abc import ABC, abstractmethod

from Tools.AiServiceBalanceQuery import get_siliconflow_balance

logger = logging.getLogger(__name__)


class RotatableClient(ABC):
    @abstractmethod
    def update_api_token(self, token: str):
        pass

    @abstractmethod
    def update_token_balance(self, token: str, balance: float):
        pass


class SiliconFlowServiceRotator:
    """
    A service for rotating Silicon Flow API keys based on balance thresholds.
    Automatically manages key validity, balance checking, and key rotation.
    Keys with insufficient balance are marked as 'disabled' and permanently excluded from use.
    """

    def __init__(self,
                 ai_client: RotatableClient,
                 keys_file: str,
                 keys_record_file: str = 'key_record.json',
                 threshold: float = 0.2,
                 check_all_balance_on_start: bool = True):
        """
        Initialize the key rotator.

        Args:
            ai_client: OpenAI compatible API client instance.
            keys_file: Path to the file containing API keys (one per line).
            threshold: Balance threshold in USD for disabling a key.
            check_all_balance_on_start: Whether to check all keys' balances on startup.
        """
        self.ai_client = ai_client
        self.keys_file = keys_file
        self.threshold = threshold
        self.check_all_balance_on_start = check_all_balance_on_start

        # Key database structure: {key: {'balance': float, 'last_used': timestamp, 'status': str}}
        # Status can be: 'unknown', 'valid', 'error', 'disabled'
        self.keys_data: Dict[str, Dict] = {}
        self.current_key: str = ''
        self.key_record_file: str = keys_record_file
        self.lock = threading.RLock()  # For thread-safe operations
        self.running: bool = False

        # Tracker for consumption rate calculation, only for the current key.
        # This is kept separate from the persistent keys_data.
        self.rate_tracker: Dict = {}

        self._load_keys()
        # Note: Key initialization is now handled at the beginning of run_forever()

    def _load_keys(self):
        """
        Load keys from key_record.json and the keys text file.
        Merges keys from both sources and maintains existing key status information.
        Keys marked as 'disabled' in the JSON file will be ignored for all future operations.
        """
        # Load existing key records from JSON file
        if os.path.exists(self.key_record_file):
            try:
                with open(self.key_record_file, 'r', encoding='utf-8') as f:
                    self.keys_data = json.load(f)
                logger.info(f"Loaded {len(self.keys_data)} keys from key record.")
            except (json.JSONDecodeError, IOError) as e:
                logger.warning(f"Failed to load key record: {e}, starting fresh.")
                self.keys_data = {}
            except Exception as e:
                logger.error(f'Unexpected error when loading {self.key_record_file}: {e}')
                self.keys_data = {}

        # Load keys from the text file and merge with existing records
        has_update = False
        if os.path.exists(self.keys_file):
            try:
                with open(self.keys_file, 'r', encoding='utf-8') as f:
                    for line in f:
                        key = line.strip()
                        if key and key not in self.keys_data:
                            self.keys_data[key] = {
                                'balance': -1.0,  # A negative value indicates an unknown balance.
                                'last_used': 0,
                                'status': 'unknown'  # Statuses: unknown, valid, error, disabled
                            }
                            has_update = True
                            logger.info(f"Added new key from file: {key[:16]}...")
            except Exception as e:
                logger.error(f'Unexpected error when loading {self.keys_file}: {e}')

        if has_update:
            self._save_key_records()

    def _select_initial_key(self):
        """
        Selects the first usable key on startup.
        It iterates through all keys that are not disabled, checks their balance,
        and picks the first one that meets the balance threshold.
        Keys that fail the balance check are marked as 'disabled'.
        All updates are saved to the JSON database at once.
        """
        logger.info("Selecting initial key...")
        records_updated = False

        # Using list() to create a copy, allowing modification of the dict during iteration.
        for key, data in list(self.keys_data.items()):
            # Request 1: Skip keys that are already known to be disabled.
            if data.get('status') == 'disabled':
                continue

            balance = self._fetch_balance_with_retry(key)
            records_updated = True  # An attempt to fetch balance means we should save the result.

            if balance is not None:
                self.keys_data[key]['balance'] = balance
                if balance < self.threshold:
                    # The key is unusable, mark it as disabled and continue to the next one.
                    self.keys_data[key]['status'] = 'disabled'
                    logger.warning(f"Key {key[:16]}... is below threshold (${balance:.4f}). Marking as disabled.")
                else:
                    # Found a usable key. Set it as current and stop searching.
                    self.keys_data[key]['status'] = 'valid'
                    self.current_key = key
                    self._change_api_key(key)
                    logger.info(f"Initialized with key: {self.current_key[:16]}... with balance ${balance:.4f}")
                    break
            else:
                # Failed to fetch balance after retries. Mark with 'error' and try the next key.
                self.keys_data[key]['status'] = 'error'
                logger.error(f"Failed to fetch balance for key {key[:16]}... Marking with status 'error'.")

        # Request 2: Save all updates made during the initial selection process.
        if records_updated:
            self._save_key_records()

        if not self.current_key:
            logger.critical("No usable API keys found after initial check!")

    def check_and_update_current_key(self):
        """
        Check the current key's balance. If it falls below the threshold,
        mark it as 'disabled' and rotate to the next available key.
        """
        if not self.current_key:
            logger.warning("No current key is set. Attempting to find a new one.")
            self._rotate_to_next_key()
            return bool(self.current_key)

        balance = self._fetch_balance_with_retry(self.current_key)

        if balance is None:
            logger.error(
                f"Failed to fetch balance for current key {self.current_key[:16]}.... Marking as 'error' and rotating.")
            with self.lock:
                self.keys_data[self.current_key]['status'] = 'error'
            self._save_key_records()
            self._rotate_to_next_key()
            return False

        logger.debug(f"Current key {self.current_key[:16]}... balance: ${balance:.4f}")

        self.ai_client.update_token_balance(self.current_key, balance)

        records_changed = False
        with self.lock:
            # Only mark as changed if balance value is actually different
            if self.keys_data[self.current_key]['balance'] != balance:
                self.keys_data[self.current_key]['balance'] = balance
                records_changed = True

            self.keys_data[self.current_key]['last_used'] = time.time()

            # Request 4: Check if the key has reached the unusable threshold.
            if balance < self.threshold:
                logger.warning(
                    f"Key {self.current_key[:16]}... balance below threshold (${balance:.4f} < ${self.threshold}). Disabling and rotating...")
                self.keys_data[self.current_key]['status'] = 'disabled'
                records_changed = True
                if records_changed:
                    self._save_key_records()  # Save immediately after disabling a key.
                return self._rotate_to_next_key()
            else:
                if self.keys_data[self.current_key]['status'] != 'valid':
                    self.keys_data[self.current_key]['status'] = 'valid'
                    records_changed = True

        if records_changed:
            self._save_key_records()
        return True

    def _fetch_balance_with_retry(self, key: str, max_retries: int = 3) -> float | None:
        """
        Fetch balance with retry logic to handle temporary network issues.

        Args:
            key: API key to check balance for.
            max_retries: Maximum number of retry attempts.

        Returns:
            The balance as a float, or None if all retries fail.
        """
        for attempt in range(max_retries):
            try:
                balance = self._fetch_balance(key)
                if balance is not None:
                    return balance

                wait_time = 2 ** attempt
                logger.warning(
                    f"Balance fetch attempt {attempt + 1} for key {key[:16]}... failed, retrying in {wait_time}s.")
                time.sleep(wait_time)

            except Exception as e:
                logger.error(f"Exception during balance fetch for key {key[:16]}... (attempt {attempt + 1}): {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 ** attempt)

        return None

    def run_in_thread(self, thread_quit_flag: Optional[threading.Event] = None) -> threading.Event:
        quit_flag = thread_quit_flag or threading.Event()
        rotator_thread = threading.Thread(
            target=self.run_forever,
            args=(quit_flag,),
            name="KeyRotatorThread",
            daemon=True
        )
        rotator_thread.start()
        return quit_flag

    def run_forever(self, thread_quit_flag: threading.Event):
        """
        Main scheduling loop for key management.
        It first selects an initial key, then periodically monitors its balance
        and handles automatic rotation.

        Args:
            thread_quit_flag: A threading.Event to signal when to stop the loop.
        """
        self.running = True
        logger.info("Starting key rotator service.")

        # Request 2: Select the initial key to use by checking balances sequentially.
        self._select_initial_key()

        # If specified, perform a one-time balance check for all other usable keys.
        if self.check_all_balance_on_start:
            self.check_all_balances()

        while self.running and not thread_quit_flag.is_set():
            try:
                # Periodically check and update the current key.
                self.check_and_update_current_key()

                # Calculate next check time based on balance.
                check_interval = self._calculate_check_interval()

                # Wait for the next check or a quit signal.
                thread_quit_flag.wait(check_interval)

            except Exception as e:
                logger.error(f"Error in key rotator loop: {e}", exc_info=True)
                time.sleep(60)  # Wait for a minute before retrying on unexpected errors.

        self.running = False
        logger.info("Key rotator service stopped.")

    def _calculate_check_interval(self) -> int:
        """
        Calculates the next check interval based on the rate of balance consumption.
        This method uses a separate instance variable `self.rate_tracker` to avoid
        polluting the persistent key data.
        """
        with self.lock:
            if not self.current_key or self.current_key not in self.keys_data:
                return 300  # Default interval if no key is active

            current_balance = self.keys_data[self.current_key].get('balance', 0.0)

            # Use the dedicated rate_tracker, falling back to current values if it's not initialized.
            previous_balance = self.rate_tracker.get('previous_balance', current_balance)
            last_check_time = self.rate_tracker.get('last_check_time', time.time())
            current_time = time.time()

            time_elapsed = current_time - last_check_time
            balance_consumed = previous_balance - current_balance

            # Update the tracker for the next calculation cycle.
            self.rate_tracker['previous_balance'] = current_balance
            self.rate_tracker['last_check_time'] = current_time

            # If no consumption, time hasn't passed, or balance increased, fall back to static intervals.
            if balance_consumed <= 0 or time_elapsed < 1:
                if current_balance < self.threshold * 5:
                    return 30
                elif current_balance < self.threshold * 10:
                    return 60
                else:
                    return 600

            consumption_rate = balance_consumed / time_elapsed
            remaining_balance = current_balance - self.threshold

            if remaining_balance <= 0:
                return 15  # Check very frequently if balance is already below threshold.

            time_to_threshold = remaining_balance / consumption_rate

            # Set interval to 20% of the estimated time to threshold, providing a safe buffer.
            check_interval = int(time_to_threshold * 0.2)

            # Enforce reasonable minimum and maximum bounds.
            min_interval = 30  # At least every 30 seconds.
            max_interval = 1800  # At most every 30 minutes.
            final_interval = max(min_interval, min(check_interval, max_interval))

            logger.info(
                f"Consumption rate: ${consumption_rate * 3600:.4f}/hour. "
                f"Est. time to threshold: {time_to_threshold / 60:.1f} mins. "
                f"Next check in: {final_interval}s."
            )

            return final_interval

    def check_all_balances(self):
        """
        Check and update the balance for all usable (i.e., not disabled) keys.
        """
        logger.info("Performing a full balance check for all usable keys.")
        # Request 3: Only update keys that are not already disabled.
        keys_to_check = [key for key, data in self.keys_data.items() if data.get('status') != 'disabled']

        if not keys_to_check:
            logger.info("No usable keys to check.")
            return

        for key in keys_to_check:
            # No need to re-check the current key if it was just validated.
            if key == self.current_key and self.keys_data[key].get('status') == 'valid':
                continue

            balance = self._fetch_balance_with_retry(key)
            with self.lock:
                if balance is not None:
                    self.keys_data[key]['balance'] = balance
                    if balance < self.threshold:
                        self.keys_data[key]['status'] = 'disabled'
                        logger.warning(f"Key {key[:16]}... is below threshold during full check. Disabling.")
                    else:
                        self.keys_data[key]['status'] = 'valid'
                else:
                    self.keys_data[key]['status'] = 'error'
                    logger.error(f"Failed to fetch balance for key {key[:16]}... during full check.")

        self._save_key_records()
        logger.info("Completed full balance check.")

    def _rotate_to_next_key(self) -> bool:
        """
        Rotate to the next usable key in the list. A usable key is one
        that is not marked as 'disabled'.

        Returns:
            True if rotation was successful, False if no usable keys are available.
        """
        usable_keys = self._get_usable_keys()

        if not usable_keys:
            logger.critical("Rotation failed: No usable keys available.")
            self.current_key = ''
            self.ai_client.update_api_token('')
            return False

        # Simply pick the first usable key from the remaining list.
        # The main loop will validate its balance in the next iteration.
        new_key = usable_keys[0]
        self.current_key = new_key
        self._change_api_key(new_key)
        logger.info(f"Rotated to new key: {new_key[:16]}...")
        return True

    def _get_usable_keys(self) -> List[str]:
        """
        Get a list of keys that are not disabled.
        This provides the pool of keys for rotation and checking.
        """
        with self.lock:
            # A key is considered usable if it has not been explicitly marked as 'disabled'.
            return [key for key, data in self.keys_data.items()
                    if data.get('status') != 'disabled']

    def _save_key_records(self):
        """
        Save the current key data to the JSON file atomically.
        Uses a temporary file to prevent data corruption on write failure.
        """
        with self.lock:
            try:
                temp_file = f"{self.key_record_file}.tmp"
                with open(temp_file, 'w', encoding='utf-8') as f:
                    json.dump(self.keys_data, f, indent=2)
                os.replace(temp_file, self.key_record_file)
                logger.debug("Key records saved successfully.")
            except Exception as e:
                logger.error(f"Failed to save key records: {e}")

    def _fetch_balance(self, key: str) -> float | None:
        """
        Fetch the balance for a specific API key from the service.

        Args:
            key: The API key to check.

        Returns:
            The balance as a float, or None if the fetch fails or the response is malformed.
        """
        if not key:
            return None

        try:
            result = get_siliconflow_balance(key)
            balance = result.get('data', {}).get('total_balance_usd')
            if balance is not None:
                return float(balance)
            else:
                logger.warning(f"Malformed balance response for key {key[:16]}...: {result}")
                return None
        except Exception as e:
            logger.error(f"Exception fetching balance for key {key[:16]}...: {e}")
            return None

    def _change_api_key(self, api_key: str):
        """
        Change the API key in the AI client and reset the consumption rate tracker.
        This method is the central point for all key changes.
        """
        try:
            self.ai_client.update_api_token(api_key)
            logger.info(f"Successfully changed API client key to: {api_key[:16]}...")

            # Reset the rate tracker whenever the key changes.
            # This ensures the consumption rate for the new key is calculated fresh.
            logger.info(f"Resetting consumption rate tracker for new key {api_key[:16]}...")
            if self.current_key and self.current_key in self.keys_data:
                # Use the last known balance as the starting point for the new key.
                current_balance = self.keys_data[self.current_key].get('balance', -1.0)
                self.rate_tracker = {
                    'previous_balance': current_balance,
                    'last_check_time': time.time()
                }
            else:
                self.rate_tracker = {}  # Clear tracker if no valid key is set.

        except Exception as e:
            logger.error(f"Failed to change API key in client: {e}")

    def stop(self):
        """Stop the key rotator service."""
        self.running = False
        logger.info("Key rotator service stopping...")

    def get_status(self) -> Dict:
        """
        Get the current status of the key rotator.

        Returns:
            A dictionary containing current status information.
        """
        with self.lock:
            usable_count = len(self._get_usable_keys())
            total_count = len(self.keys_data)
            current_balance = self.keys_data.get(self.current_key, {}).get('balance', 'N/A') if self.current_key else 'N/A'

            return {
                'running': self.running,
                'current_key': self.current_key[:16] + '...' if self.current_key else 'None',
                'current_balance': current_balance,
                'usable_keys': usable_count,
                'total_keys': total_count,
                'threshold': self.threshold,
            }
