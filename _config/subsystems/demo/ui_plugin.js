/* _config/subsystems/demo/ui_plugin.js
 * ?????????card/summary?????????
 * ?? summary ???? Demo ???????????????
 */
(function () {
  if (typeof window.SubsystemUI === 'undefined') {
    console.warn('[demo] SubsystemUI protocol not loaded.');
    return;
  }

  window.SubsystemUI.register({
    name: 'demo',
    blocks: {
      // ?????????????? Demo ????????????????
      'card/summary': function (doc, helpers) {
        var brief = doc && (doc.EVENT_BRIEF || doc.brief || 'No Brief');
        var esc = (helpers && helpers.escapeHTML) ? helpers.escapeHTML : function (s) { return String(s); };
        return '<p class="article-summary"><span class="demo-plugin-badge" style="'
          + 'display:inline-block;margin-right:6px;padding:0 6px;font-size:11px;'
          + 'border-radius:4px;background:#e8f0fe;color:#1a73e8;'
          + '">Demo Plugin</span>' + esc(brief) + '</p>';
      }
    }
  });
})();
