/* 前端只展示后端已经选定的契约值，不重新实现业务判定。 */
(function () {
    if (window.IntelligenceContract) return;

    const ContentTimeSource = Object.freeze({
        PUBLISHED: 'published',
        COLLECTED: 'collected',
        ARCHIVED: 'archived'
    });
    const labels = Object.freeze({
        en: Object.freeze({
            published: 'Published',
            collected: 'Collected',
            archived: 'Archived fallback'
        }),
        zh: Object.freeze({
            published: '发布时间',
            collected: '采集时间',
            archived: '归档时间（兜底）'
        })
    });

    window.IntelligenceContract = Object.freeze({
        ContentTimeSource,
        contentTimeLabel(source, language = 'en') {
            const localized = labels[language] || labels.en;
            return localized[source] || (language === 'zh' ? '情报时间' : 'Content time');
        }
    });
})();
