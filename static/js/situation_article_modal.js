/* 地域、实体和实体时间线共用情报列表的详情弹窗。 */
(function () {
    function init(options = {}) {
        const domains = Array.isArray(options.domains) ? options.domains : [];
        const domainName = String(options.domainName || '');
        const descriptor = domains.find(item => item.name === domainName) || {};
        const prefix = String(descriptor.url_prefix || '').replace(/\/$/, '');
        const origin = String(options.detailOrigin || '').replace(/\/$/, '');

        window.IIS_BASE_PATH = prefix;
        window.IIS_DOMAIN = domainName;
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
