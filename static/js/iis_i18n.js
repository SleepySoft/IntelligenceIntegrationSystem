(function (global) {
    'use strict';

    const messages = global.IIS_I18N_MESSAGES || {};

    function t(key, params = {}) {
        const template = Object.prototype.hasOwnProperty.call(messages, key)
            ? String(messages[key]) : String(key);
        return template.replace(/\{([A-Za-z0-9_]+)\}/g, (match, name) => (
            Object.prototype.hasOwnProperty.call(params, name)
                ? String(params[name]) : match
        ));
    }

    global.IIS_I18N = Object.freeze({
        locale: global.IIS_UI_LOCALE || document.documentElement.lang || 'zh-CN',
        t,
    });
})(window);
