/**
 * static/js/intelligence_search.js
 */

document.addEventListener('DOMContentLoaded', () => {

    // --- 1. 初始化渲染器 ---
    const renderer = new ArticleRenderer('article-list-content', 'pagination-container');
    if (window.ArticleModalManager) {
        ArticleModalManager.init({ history: false });
    }

    const searchForm = document.getElementById('search-form');
    const searchButton = document.getElementById('search-button');
    const spinner = searchButton.querySelector('.spinner-border');
    const resultsWrapper = document.getElementById('results-wrapper');
    const resultsCountEl = document.getElementById('results-count');
    const resultsTotalEl = document.getElementById('results-total');

    let currentQueryState = {
        page: 1,
        per_page: 10,
        search_mode: 'mongo',
        payload_cache: {}
    };

    // --- 2. 数据源列表 ---
    const MEDIA_SOURCES = [
        { domain: 'aa.com.tr',      name: '阿纳多卢通讯社',       flag: '🇹🇷' },
        { domain: 'abc.net.au',     name: '澳大利亚广播公司',     flag: '🇦🇺' },
        { domain: 'aljazeera.com',  name: '半岛电视台',           flag: '🇶🇦' },
        { domain: 'bbc.com',        name: '英国广播公司',         flag: '🇬🇧' },
        { domain: 'cbc.ca',         name: '加拿大广播公司',       flag: '🇨🇦' },
        { domain: 'chinanews.com',  name: '中国新闻网',           flag: '🇨🇳' },
        { domain: 'dw.com',         name: '德国之声',             flag: '🇩🇪' },
        { domain: 'elpais.com',     name: '国家报',               flag: '🇪🇸' },
        { domain: 'france24.com',   name: '法国24台',             flag: '🇫🇷' },
        { domain: 'investing.com',  name: '英为财情',             flag: '🌍' },
        { domain: 'news.cn',        name: '新华网',               flag: '🇨🇳' },
        { domain: 'nhk.or.jp',      name: '日本广播协会',         flag: '🇯🇵' },
        { domain: 'ntv.com.tr',     name: '土耳其NTV',            flag: '🇹🇷' },
        { domain: 'rfi.fr',         name: '法国国际广播电台',     flag: '🇫🇷' },
        { domain: 'tass.com',       name: '塔斯社',               flag: '🇷🇺' },
        { domain: 'voanews.com',    name: '美国之音',             flag: '🇺🇸' },
        { domain: 'yna.co.kr',      name: '韩联社',               flag: '🇰🇷' },
    ];

    let selectedDomainsMongo = new Set();
    let selectedDomainsVector = new Set();

    function renderSourceTags(containerId, selectedSet) {
        const container = document.getElementById(containerId);
        if (!container) return;
        container.innerHTML = '';
        MEDIA_SOURCES.forEach(item => {
            const tag = document.createElement('div');
            tag.className = 'source-tag';
            tag.dataset.domain = item.domain;
            tag.title = item.domain;
            tag.innerHTML = `<span>${item.flag}</span><span>${item.name}</span><span class="check-icon">✓</span>`;
            tag.addEventListener('click', () => {
                tag.classList.toggle('selected');
                if (tag.classList.contains('selected')) selectedSet.add(item.domain);
                else selectedSet.delete(item.domain);
            });
            container.appendChild(tag);
        });
    }
    renderSourceTags('source-domains-container', selectedDomainsMongo);
    renderSourceTags('source-domains-container-vector', selectedDomainsVector);

    // --- 3. 双滑块组件 ---
    function initRangeSlider(containerId, onChange) {
        const container = document.getElementById(containerId);
        if (!container) return null;
        const minVal = parseFloat(container.dataset.min);
        const maxVal = parseFloat(container.dataset.max);
        const step = parseFloat(container.dataset.step) || 1;
        const decimal = parseInt(container.dataset.decimal) || 0;

        let low = minVal;
        let high = maxVal;

        const fill = document.createElement('div');
        fill.className = 'range-fill';
        container.appendChild(fill);

        const thumbLow = document.createElement('div');
        thumbLow.className = 'range-thumb';
        container.appendChild(thumbLow);

        const thumbHigh = document.createElement('div');
        thumbHigh.className = 'range-thumb';
        container.appendChild(thumbHigh);

        function updateUI() {
            const lowPct = (low - minVal) / (maxVal - minVal) * 100;
            const highPct = (high - minVal) / (maxVal - minVal) * 100;
            thumbLow.style.left = lowPct + '%';
            thumbHigh.style.left = highPct + '%';
            fill.style.left = lowPct + '%';
            fill.style.width = (highPct - lowPct) + '%';
            if (onChange) onChange(low, high);
        }

        function getValueFromEvent(e) {
            const rect = container.getBoundingClientRect();
            const clientX = e.touches ? e.touches[0].clientX : e.clientX;
            let pct = (clientX - rect.left) / rect.width;
            pct = Math.max(0, Math.min(1, pct));
            let val = minVal + pct * (maxVal - minVal);
            val = Math.round(val / step) * step;
            val = parseFloat(val.toFixed(decimal));
            return Math.max(minVal, Math.min(maxVal, val));
        }

        let activeThumb = null;

        function onPointerDown(e, thumb) {
            e.preventDefault();
            activeThumb = thumb;
            document.addEventListener('mousemove', onPointerMove);
            document.addEventListener('mouseup', onPointerUp);
            document.addEventListener('touchmove', onPointerMove, { passive: false });
            document.addEventListener('touchend', onPointerUp);
        }

        function onPointerMove(e) {
            if (!activeThumb) return;
            e.preventDefault();
            let val = getValueFromEvent(e);
            if (activeThumb === thumbLow) {
                if (val > high - step) val = parseFloat((high - step).toFixed(decimal));
                low = val;
            } else {
                if (val < low + step) val = parseFloat((low + step).toFixed(decimal));
                high = val;
            }
            updateUI();
        }

        function onPointerUp() {
            activeThumb = null;
            document.removeEventListener('mousemove', onPointerMove);
            document.removeEventListener('mouseup', onPointerUp);
            document.removeEventListener('touchmove', onPointerMove);
            document.removeEventListener('touchend', onPointerUp);
        }

        thumbLow.addEventListener('mousedown', e => onPointerDown(e, thumbLow));
        thumbHigh.addEventListener('mousedown', e => onPointerDown(e, thumbHigh));
        thumbLow.addEventListener('touchstart', e => onPointerDown(e, thumbLow), { passive: false });
        thumbHigh.addEventListener('touchstart', e => onPointerDown(e, thumbHigh), { passive: false });

        container.addEventListener('click', e => {
            if (e.target.classList.contains('range-thumb')) return;
            const val = getValueFromEvent(e);
            const distLow = Math.abs(val - low);
            const distHigh = Math.abs(val - high);
            if (distLow < distHigh) {
                low = Math.min(val, parseFloat((high - step).toFixed(decimal)));
            } else {
                high = Math.max(val, parseFloat((low + step).toFixed(decimal)));
            }
            updateUI();
        });

        updateUI();
        return { setValues: (l, h) => { low = l; high = h; updateUI(); } };
    }

    const sliderMongo = initRangeSlider('slider-mongo', (low, high) => {
        document.getElementById('threshold-min-mongo').value = low;
        document.getElementById('threshold-max-mongo').value = high;
        document.getElementById('score-label-mongo').textContent = `${low} - ${high}`;
    });

    const sliderVector = initRangeSlider('slider-vector', (low, high) => {
        document.getElementById('score-threshold-min-vector').value = low;
        document.getElementById('score-threshold-max-vector').value = high;
        document.getElementById('score-label-vector').textContent = `${low.toFixed(2)} - ${high.toFixed(2)}`;
    });

    // --- 4. 日期选择器 ---
    function initFlatpickr(inputId, startHiddenId, endHiddenId) {
        const input = document.getElementById(inputId);
        const startHidden = document.getElementById(startHiddenId);
        const endHidden = document.getElementById(endHiddenId);
        if (!input || typeof flatpickr === 'undefined') return null;
        const now = new Date();
        const yesterday = new Date(now.getTime() - 24 * 60 * 60 * 1000);
        const fp = flatpickr(input, {
            mode: "range",
            enableTime: true,
            dateFormat: "Y-m-d H:i",
            time_24hr: true,
            defaultDate: [yesterday, now],
            onChange: function(selectedDates) {
                if (selectedDates.length >= 2) {
                    startHidden.value = flatpickr.formatDate(selectedDates[0], "Y-m-d H:i") + ':00';
                    endHidden.value = flatpickr.formatDate(selectedDates[1], "Y-m-d H:i") + ':00';
                } else {
                    startHidden.value = '';
                    endHidden.value = '';
                }
            }
        });
        // 初始化时同步一次 hidden input
        if (fp.selectedDates.length >= 2) {
            startHidden.value = flatpickr.formatDate(fp.selectedDates[0], "Y-m-d H:i") + ':00';
            endHidden.value = flatpickr.formatDate(fp.selectedDates[1], "Y-m-d H:i") + ':00';
        }
        return fp;
    }

    const fpMongo = initFlatpickr('date-range-mongo', 'start-time-mongo', 'end-time-mongo');
    const fpVector = initFlatpickr('date-range-vector', 'start-time-vector', 'end-time-vector');

    // --- 5. 核心搜索功能 ---
    async function fetchResults(payload) {
        searchButton.disabled = true;
        spinner.classList.remove('d-none');
        resultsWrapper.style.display = 'block';
        renderer.showLoading();

        try {
            const response = await fetch('/intelligences/query', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(payload),
            });

            if (!response.ok) {
                const errorData = await response.json().catch(() => ({}));
                throw new Error(errorData.error || `Server Error: ${response.status}`);
            }

            const data = await response.json();
            renderer.render(data.results, {
                total: data.total,
                page: payload.page,
                per_page: payload.per_page
            });
            resultsCountEl.textContent = data.results.length;
            resultsTotalEl.textContent = data.total;

            if (payload.page > 1) {
                resultsWrapper.scrollIntoView({ behavior: 'smooth' });
            }
        } catch (error) {
            console.error('Fetch error:', error);
            renderer.showError(error.message);
            resultsTotalEl.textContent = '0';
            resultsCountEl.textContent = '0';
        } finally {
            searchButton.disabled = false;
            spinner.classList.add('d-none');
        }
    }

    // --- 6. 表单提交 ---
    searchForm.addEventListener('submit', (e) => {
        e.preventDefault();

        const activeTabBtn = document.querySelector('#search-mode-tabs .nav-link.active');
        const rawMode = activeTabBtn ? activeTabBtn.dataset.mode : 'mongo';
        const searchMode = rawMode === 'vector' ? 'vector_text' : 'mongo';

        const payload = {
            page: 1,
            per_page: 10,
            search_mode: searchMode,
            keywords: ''
        };

        if (searchMode === 'vector_text') {
            // Vector 模式
            const kw = document.querySelector('#vector-pane textarea[name="keywords_vector"]');
            payload.keywords = kw ? kw.value.trim() : '';

            const perPage = document.querySelector('#vector-pane select[name="per_page"]');
            payload.per_page = Number(perPage ? perPage.value : 10) || 10;

            const st = document.getElementById('start-time-vector').value;
            const et = document.getElementById('end-time-vector').value;
            if (st) payload.start_time = st;
            if (et) payload.end_time = et;

            const scoreMin = document.getElementById('score-threshold-min-vector').value;
            const scoreMax = document.getElementById('score-threshold-max-vector').value;
            payload.score_threshold_min = Number(scoreMin);
            payload.score_threshold_max = Number(scoreMax);

            const inSummary = document.querySelector('#vector-pane input[name="in_summary"]');
            const inFulltext = document.querySelector('#vector-pane input[name="in_fulltext"]');
            payload.in_summary = inSummary ? inSummary.checked : true;
            payload.in_fulltext = inFulltext ? inFulltext.checked : false;

            if (selectedDomainsVector.size > 0) {
                payload.informant_domains = Array.from(selectedDomainsVector).join(',');
            }
        } else {
            // Mongo 模式
            const kw = document.querySelector('#mongo-pane input[name="keywords"]');
            payload.keywords = kw ? kw.value.trim() : '';

            const perPage = document.querySelector('#mongo-pane select[name="per_page"]');
            payload.per_page = Number(perPage ? perPage.value : 10) || 10;

            const st = document.getElementById('start-time-mongo').value;
            const et = document.getElementById('end-time-mongo').value;
            if (st) payload.start_time = st;
            if (et) payload.end_time = et;

            const thMin = document.getElementById('threshold-min-mongo').value;
            const thMax = document.getElementById('threshold-max-mongo').value;
            payload.threshold_min = Number(thMin);
            payload.threshold_max = Number(thMax);

            const loc = document.querySelector('#mongo-pane input[name="locations"]');
            const geo = document.querySelector('#mongo-pane input[name="geography"]');
            const peo = document.querySelector('#mongo-pane input[name="peoples"]');
            const org = document.querySelector('#mongo-pane input[name="organizations"]');
            if (loc && loc.value.trim()) payload.locations = loc.value.trim();
            if (geo && geo.value.trim()) payload.geography = geo.value.trim();
            if (peo && peo.value.trim()) payload.peoples = peo.value.trim();
            if (org && org.value.trim()) payload.organizations = org.value.trim();

            if (selectedDomainsMongo.size > 0) {
                payload.informant_domains = Array.from(selectedDomainsMongo).join(',');
            }
        }

        currentQueryState.payload_cache = payload;
        currentQueryState.page = 1;
        currentQueryState.search_mode = searchMode;

        fetchResults(payload);
    });

    // --- 7. 分页点击 ---
    document.body.addEventListener('click', (e) => {
        const target = e.target.closest('.page-btn');
        if (target && !target.classList.contains('disabled')) {
            e.preventDefault();
            const clickPage = parseInt(target.dataset.page);
            if (clickPage && clickPage !== currentQueryState.page) {
                const nextPayload = { ...currentQueryState.payload_cache };
                nextPayload.page = clickPage;
                currentQueryState.page = clickPage;
                fetchResults(nextPayload);
            }
        }
    });

    // --- 8. URL 参数自动填充与自动搜索 ---
    function applyUrlParams() {
        const urlParams = new URLSearchParams(window.location.search);
        if (!urlParams.toString()) return;

        const mode = urlParams.get('mode') || 'mongo';
        const isVector = mode === 'vector';

        // 切换 Tab
        if (isVector) {
            const vectorTabBtn = document.getElementById('vector-tab');
            if (vectorTabBtn && typeof bootstrap !== 'undefined') {
                const tab = new bootstrap.Tab(vectorTabBtn);
                tab.show();
            }
        }

        // 填充关键词
        const kw = urlParams.get('keywords');
        if (kw) {
            if (isVector) {
                const el = document.querySelector('#vector-pane textarea[name="keywords_vector"]');
                if (el) el.value = kw;
            } else {
                const el = document.querySelector('#mongo-pane input[name="keywords"]');
                if (el) el.value = kw;
            }
        }

        // 填充实体字段
        const entities = {
            'locations': 'locations',
            'peoples': 'peoples',
            'organizations': 'organizations',
            'geography': 'geography'
        };
        for (const [param, fieldName] of Object.entries(entities)) {
            const val = urlParams.get(param);
            if (val) {
                const el = document.querySelector(`#${isVector ? 'vector' : 'mongo'}-pane input[name="${fieldName}"]`);
                if (el) el.value = val;
            }
        }

        // 填充日期：直接设置 hidden input 用 URL 原始值，flatpickr 仅更新显示不触发 onChange
        const startTime = urlParams.get('start_time');
        const endTime = urlParams.get('end_time');
        if (startTime && endTime) {
            const parseDate = (s) => {
                s = s.replace(' ', 'T');
                const d = new Date(s);
                return isNaN(d) ? null : d;
            };
            const startDate = parseDate(startTime);
            const endDate = parseDate(endTime);
            if (!isVector) {
                document.getElementById('start-time-mongo').value = startTime;
                document.getElementById('end-time-mongo').value = endTime;
                if (fpMongo && startDate && endDate) {
                    fpMongo.setDate([startDate, endDate], false);
                }
            } else {
                document.getElementById('start-time-vector').value = startTime;
                document.getElementById('end-time-vector').value = endTime;
                if (fpVector && startDate && endDate) {
                    fpVector.setDate([startDate, endDate], false);
                }
            }
        }

        // 填充分数段
        if (!isVector) {
            const tMin = parseFloat(urlParams.get('threshold_min'));
            const tMax = parseFloat(urlParams.get('threshold_max'));
            if (!isNaN(tMin) && !isNaN(tMax) && sliderMongo) {
                sliderMongo.setValues(tMin, tMax);
            }
        } else {
            const sMin = parseFloat(urlParams.get('score_threshold_min'));
            const sMax = parseFloat(urlParams.get('score_threshold_max'));
            if (!isNaN(sMin) && !isNaN(sMax) && sliderVector) {
                sliderVector.setValues(sMin, sMax);
            }
        }

        // 填充数据源标签
        const domains = urlParams.get('informant_domains');
        if (domains) {
            const domainList = domains.split(',').map(d => d.trim()).filter(Boolean);
            const selectedSet = isVector ? selectedDomainsVector : selectedDomainsMongo;
            const containerId = isVector ? 'source-domains-container-vector' : 'source-domains-container';
            const container = document.getElementById(containerId);
            if (container) {
                domainList.forEach(domain => {
                    const tag = container.querySelector(`.source-tag[data-domain="${domain}"]`);
                    if (tag) {
                        tag.classList.add('selected');
                        selectedSet.add(domain);
                    }
                });
            }
        }

        // 自动搜索
        if (urlParams.get('auto_search') === '1') {
            searchForm.dispatchEvent(new Event('submit'));
        }
    }
    applyUrlParams();

});
