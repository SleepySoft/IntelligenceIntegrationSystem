/* 地域、实体和实体时间线共用情报列表的详情弹窗。 */
(function () {
    function init(options = {}) {
        const subsystems = Array.isArray(options.subsystems) ? options.subsystems : [];
        const subsystemName = String(options.subsystemName || '');
        const descriptor = subsystems.find(item => item.name === subsystemName) || {};
        const prefix = String(descriptor.url_prefix || '').replace(/\/$/, '');
        const origin = String(options.detailOrigin || '').replace(/\/$/, '');

        window.IIS_BASE_PATH = prefix;
        window.IIS_SUBSYSTEM = subsystemName;
        window.IIS_VECTOR_ENABLED = false;
        window.IIS_UI_PLUGIN_ENABLED = false;

        if (!window.ArticleModalManager || !window.ArticleDetailRenderer) {
            console.error('[SituationArticleModal] detail modal dependencies are unavailable.');
            return null;
        }
        return window.ArticleModalManager.init({
            apiBase: `${origin}${prefix}/api/intelligence`,
            pageBase: `${origin}${prefix}/intelligence`,
            titleSelector: 'a.situation-article-title[data-uuid]',
            history: false,
        });
    }

    window.SituationArticleModal = { init };
})();
