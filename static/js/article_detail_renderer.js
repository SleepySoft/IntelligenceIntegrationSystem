/* static/js/article_detail_renderer.js */

window.ArticleDetailRenderer = {
    // 基础防 XSS 转义（正文需要渲染 HTML 时不使用这个）
    escapeHTML: function(str) {
        if (!str) return '';
        return String(str).replace(/[&<>"']/g, function(m) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[m];
        });
    },

    anyTimeToTimeStr: function(s) {
        try {
            const d = new Date(s);
            if (isNaN(d.getTime())) return s || '无时间信息';
            const y = d.getFullYear();
            const m = String(d.getMonth()+1).padStart(2,'0');
            const day = String(d.getDate()).padStart(2,'0');
            const hh = String(d.getHours()).padStart(2,'0');
            const mm = String(d.getMinutes()).padStart(2,'0');
            const ss = String(d.getSeconds()).padStart(2,'0');
            return `${y}-${m}-${day} ${hh}:${mm}:${ss}`;
        } catch { return s || '无时间信息'; }
    },

    renderStars: function(score) {
        const num = Number(score);
        if (!Number.isFinite(num)) return '';
        const full = Math.floor(num / 2);
        const half = (num % 2) >= 1;
        const empty = 5 - full - (half ? 1 : 0);
        let html = '<span class="stars">';
        for (let i = 0; i < full; i++) html += '<i class="bi bi-star-fill text-warning"></i> ';
        if (half) html += '<i class="bi bi-star-half text-warning"></i> ';
        for (let i = 0; i < empty; i++) html += '<i class="bi bi-star text-warning"></i> ';
        html += ` <span class="ms-2 text-muted">${num}/10</span></span>`;
        return html;
    },

    renderRatingTable: function(article) {
        const rates = article?.RATE || {};
        const manualRatings = article?.manual_rating || {};
        const keys = Object.keys(rates);
        if (!keys.length) return '';

        let rows = '';
        keys.forEach(key => {
            const score = rates[key];
            if (typeof score !== 'number' || score < 0 || score > 10) return;
            const manual = (manualRatings && manualRatings[key] != null) ? manualRatings[key] : score;
            rows += `
                <tr>
                    <td>${this.escapeHTML(key)}</td>
                    <td>${this.renderStars(score)}</td>
                    <td>
                        <input id="rating-${this.escapeHTML(key)}" type="number" class="form-control form-control-sm"
                            value="${manual}" min="0" max="10" step="0.5" style="width: 80px;"
                            oninput="if(this.value<0)this.value=0;if(this.value>10)this.value=10;">
                    </td>
                </tr>`;
        });

        return `
            <div class="mt-4">
                <h5><i class="bi bi-graph-up"></i> 分析与评分</h5>
                <div class="table-responsive">
                    <table class="table table-sm">
                        <thead><tr><th>维度</th><th>AI 评分</th><th>人工评分</th></tr></thead>
                        <tbody>${rows}</tbody>
                    </table>
                </div>
                <div class="mt-3">
                    <button id="submit-rating" class="btn btn-primary btn-sm">
                        <i class="bi bi-check-circle"></i> 提交人工评分
                    </button>
                </div>
            </div>`;
    },

    humanLabel: function(value, labels = {}) {
        if (value == null || value === '') return '未说明';
        const text = String(value);
        return labels[text] || text.replace(/_/g, ' ');
    },

    // 事件标题优先使用 registry 中文标签；surface 是原文措辞（可能是外语），
    // 作为证据锚点保留在技术字段中，不充当面向用户的标题。
    predicateTitle: function(predicate, fallback = '未命名事件') {
        const p = predicate || {};
        return p.label || p.gloss || p.surface || p.id || fallback;
    },

    eventEntities: function(article) {
        const result = {};
        const add = (key, entity) => {
            if (!key || !entity) return;
            result[String(key)] = entity;
        };
        (article?.EVENT_EXTRACTION?.entities || []).forEach(entity => add(entity?.id, {
            canonical_name: entity?.name,
            entity_type: entity?.type,
            country_code: entity?.country_code
        }));
        return result;
    },

    normalizedEvents: function(article) {
        if (Array.isArray(article?.events) && article.events.length) return article.events;
        const extraction = article?.EVENT_EXTRACTION;
        if (!Array.isArray(extraction?.events)) return [];
        return extraction.events.map(event => {
            const core = event?.core || {};
            const entityMap = this.eventEntities(article);
            const snapshot = entityId => entityMap[String(entityId)] || {name: entityId};
            return {
                event_uuid: event.id,
                local_event_id: event.id,
                is_primary: event.id === extraction.primary_event_id,
                frame: core.frame,
                predicate: core.predicate,
                roles: Object.fromEntries(Object.entries(core.roles || {}).map(
                    ([role, entityIds]) => [role, (entityIds || []).map(snapshot)])),
                time: core.time || {},
                event_location: (core.context?.event_location || []).map(snapshot),
                attributes: core.attributes || {},
                qualifiers: (event.qualifiers || []).map(value => ({
                    ...value,
                    by: (value.by || []).map(snapshot)
                })),
                relations: (event.relations || []).map(relation => ({
                    predicate: relation.predicate,
                    target_event_uuid: relation.target_event_id,
                    surface: relation.surface
                }))
            };
        });
    },

    countryDisplayName: function(countryCode) {
        const code = String(countryCode || '').toUpperCase();
        if (!code) return '';
        try {
            if (typeof Intl !== 'undefined' && Intl.DisplayNames) {
                return new Intl.DisplayNames(['zh-CN'], {type: 'region'}).of(code) || code;
            }
        } catch (_) {
            // 旧浏览器或无效代码回退到原始 ISO 代码。
        }
        return code;
    },

    renderEntityChip: function(entityValue, entityMap = {}) {
        const entity = typeof entityValue === 'object'
            ? entityValue : (entityMap[String(entityValue)] || {});
        const name = entity.canonical_name || entity.name || String(entityValue || '未知实体');
        const entityTypeLabels = {
            person: '人物', organization: '机构', geopolitical_entity: '国家/地区',
            location: '地点', facility: '设施', equipment: '设备', product: '产品',
            resource: '资源', asset: '资产', information: '信息', policy: '政策',
            agreement: '协议', position: '职位', capability: '能力', topic: '议题',
            phenomenon: '现象', case: '案件', event: '事件', other_object: '其他对象',
            law: '法规', other: '其他'
        };
        const rawType = entity.entity_type || entity.type;
        const typeLabel = rawType ? this.humanLabel(rawType, entityTypeLabels) : '';
        const countryName = this.countryDisplayName(entity.country_code);
        const physicalTypes = new Set(['location', 'facility', 'equipment', 'product', 'resource', 'asset']);
        const repeatsEntityName = countryName && String(name).includes(countryName);
        const countryContext = countryName && !repeatsEntityName
            ? `${countryName}${physicalTypes.has(rawType) ? '境内' : ''}` : '';
        const extras = [countryContext, typeLabel].filter(Boolean);
        const metadata = extras.length
            ? `<small>${this.escapeHTML(extras.join(' · '))}</small>` : '';
        return `<span class="event-entity-chip"><b>${this.escapeHTML(name)}</b>${metadata}</span>`;
    },

    renderEventTime: function(time) {
        const labels = {
            event_time: '发生时间', start_time: '开始时间', end_time: '结束时间',
            effective_time: '生效时间', deadline: '截止时间',
            expected_start_time: '预计开始', expected_end_time: '预计结束'
        };
        return Object.entries(time || {}).map(([kind, value]) => {
            if (!value) return '';
            const raw = value.normalized || value.surface || value;
            const approximate = value.approximate ? '（约）' : '';
            const surface = value.surface && value.surface !== raw
                ? `<span class="event-origin-text">原文：${this.escapeHTML(value.surface)}</span>` : '';
            const precision = value.precision
                ? `<small>精度：${this.escapeHTML(value.precision)}</small>` : '';
            return `<span class="event-fact"><b>${this.escapeHTML(this.humanLabel(kind, labels))}：</b>${this.escapeHTML(raw)}${approximate}${precision}${surface}</span>`;
        }).filter(Boolean).join('');
    },

    renderEventAttributes: function(attributes) {
        const labels = {
            amount: '金额', quantity: '数量', ratio: '比例', value_before: '变化前',
            value_after: '变化后', delta: '变化量', duration: '持续时间', level: '等级/水平'
        };
        return Object.entries(attributes || {}).map(([name, attribute]) => {
            if (!attribute) return '';
            let value;
            if (attribute.type === 'money') {
                value = [attribute.value, attribute.unit, attribute.currency].filter(v => v != null && v !== '').join(' ');
            } else if (attribute.type === 'ratio' && Number.isFinite(Number(attribute.value))) {
                value = `${Number(attribute.value) * 100}%`;
            } else if (typeof attribute === 'object') {
                value = [attribute.value, attribute.unit, attribute.currency].filter(v => v != null && v !== '').join(' ');
            } else {
                value = String(attribute);
            }
            const surface = attribute?.surface && String(attribute.surface) !== String(value)
                ? `<span class="event-origin-text">原文：${this.escapeHTML(attribute.surface)}</span>` : '';
            const type = attribute?.type
                ? `<small>类型：${this.escapeHTML(attribute.type)}</small>` : '';
            return `<span class="event-fact"><b>${this.escapeHTML(this.humanLabel(name, labels))}：</b>${this.escapeHTML(value || '未说明')}${type}${surface}</span>`;
        }).filter(Boolean).join('');
    },

    renderEventSection: function(article) {
        const events = this.normalizedEvents(article);
        if (!events.length) return '';

        const roleLabels = {
            actor: '行动方', target: '目标', subject: '主体', counterpart: '关联方',
            agent: '执行方', affected: '受影响方', authority: '主管方', person: '人员',
            organization: '组织', party: '参与方', participant: '参与方', belligerent: '交战方',
            source: '来源方', destination: '接收方', provider: '提供方', recipient: '接收方',
            theme: '转移对象', object: '对象', asset: '资产', instrument: '手段/工具',
            location: '地点', affected_area: '受影响地区', topic: '议题', position: '职位',
            holder: '持有方', controller: '控制方', controlled: '受控对象', member: '成员',
            dependent: '依赖方', dependency: '依赖对象', discoverer: '发现方', rule: '规则',
            goods: '物资/商品', sender: '发送方', information: '信息', inspector: '检查方',
            investigator: '调查方', agreement: '协议', electorate: '选民', case: '案件',
            releaser: '发布方', phenomenon: '现象', debtor: '债务人', obligation: '债务',
            buyer: '买方', seller: '卖方', acquirer: '收购方', investor: '投资方',
            resource: '资源', payer: '付款方', payee: '收款方', lender: '贷款方',
            borrower: '借款方', creditor: '债权人', issuer: '发行方', security: '证券',
            rater: '评级方', supplier: '供应方', operator: '运营方', producer: '生产方',
            product: '产品', builder: '建设方', developer: '开发方', tester: '测试方',
            deployer: '部署方', facility: '设施', capability: '能力'
        };
        const semanticLabels = {
            agent: '行动者', affected: '受作用者', participant: '参与者', theme: '主题对象',
            source: '来源端', destination: '去向端', instrument: '工具', authority: '主管主体',
            beneficiary: '受益方', location: '地点', other: '其他'
        };
        const qualifierTypeLabels = {
            phase: '阶段', intention: '意图', authorization: '授权', directive: '指令',
            epistemic: '信息状态', modality: '可能性', polarity: '极性'
        };
        const qualifierValueLabels = {
            asserted: '已陈述', estimated: '估计', doubted: '存疑/未证实', denied: '已否认',
            possible: '可能', probable: '很可能', conditional: '有条件', negated: '否定',
            not_started: '未开始', planned: '计划中', ongoing: '进行中', paused: '已暂停',
            suspended: '已中止', completed: '已完成', cancelled: '已取消', terminated: '已终止',
            confirmed: '已确认', pending: '待批准', approved: '已批准', revoked: '已撤销',
            ordered: '已下令', rescinded: '已撤销指令'
        };
        const relationLabels = {
            causes: '导致', promotes: '促进', prevents: '阻止', aggravates: '加剧',
            mitigates: '缓解', precedes: '先于', follows: '后于', overlaps: '时间重叠',
            condition_for: '是其条件', part_of: '属于'
        };
        const frameLabels = {
            state: '状态', process: '过程', change: '变化', intrinsic: '单体事件',
            relational: '关系事件', targeted: '指向目标', transfer: '转移事件',
            agentive: '有行动主体', non_agentive: '非行动主体', unknown: '主体未知'
        };
        const localIdByUuid = {};
        events.forEach(event => {
            localIdByUuid[String(event?.event_uuid || event?.local_event_id || '')] = event?.local_event_id;
        });
        const eventKey = event => String(event?.event_uuid || event?.local_event_id || '');
        const primaryEvent = events.find(event => event?.is_primary) || events[0];
        const primaryKey = eventKey(primaryEvent);
        const extraction = article?.EVENT_EXTRACTION || {};
        const eventEntities = events.flatMap(event => [
            ...Object.values(event?.roles || {}).flat(),
            ...(event?.event_location || []),
            ...(event?.qualifiers || []).flatMap(qualifier => qualifier.by || [])
        ]);
        const entityCount = Array.isArray(extraction.entities)
            ? extraction.entities.length
            : new Set(eventEntities.map(entity =>
                `${entity?.entity_type || entity?.type || ''}:${entity?.canonical_name || entity?.name || ''}`)).size;

        const renderFrame = event => {
            const frame = event?.frame || {};
            return ['dynamics', 'topology', 'agency'].map(key => frame[key]
                ? `<span class="event-frame-badge"><small>${this.escapeHTML(key)}</small>${this.escapeHTML(this.humanLabel(frame[key], frameLabels))}</span>`
                : '').join('');
        };
        const renderRoles = event => Object.entries(event?.roles || {}).map(([roleName, entities]) => {
            const semanticGroup = event?.role_semantics?.[roleName];
            const semantic = semanticGroup
                ? `<small>${this.escapeHTML(this.humanLabel(semanticGroup, semanticLabels))}</small>` : '';
            const chips = (entities || []).map(entity => this.renderEntityChip(entity)).join('');
            return `<div class="event-role-card"><span>${this.escapeHTML(this.humanLabel(roleName, roleLabels))}${semantic}</span><div>${chips}</div></div>`;
        }).join('');
        const renderQualifiers = event => (event?.qualifiers || []).map(qualifier => {
            const by = (qualifier.by || []).map(value => this.renderEntityChip(value)).join('');
            const byNames = (qualifier.by || []).map(value =>
                value?.canonical_name || value?.name || String(value || '')).filter(Boolean).join('、');
            const qualifierTime = qualifier.time
                ? [
                    qualifier.time.normalized || qualifier.time.surface || '',
                    qualifier.time.precision ? `精度 ${qualifier.time.precision}` : '',
                    qualifier.time.approximate ? '约' : ''
                ].filter(Boolean).join(' · ') : '';
            const surface = qualifier.surface
                ? `<span class="event-origin-text">原文：${this.escapeHTML(qualifier.surface)}</span>` : '';
            return `<div class="event-qualifier-card">
                <div><b>${this.escapeHTML(byNames || '报道')}对这件事的表述</b><span>${this.escapeHTML(this.humanLabel(qualifier.value, qualifierValueLabels))}</span></div>
                ${surface}
                <details class="event-qualifier-technical"><summary>限定信息</summary><dl>
                    <div><dt>类别</dt><dd>${this.escapeHTML(this.humanLabel(qualifier.type, qualifierTypeLabels))}</dd></div>
                    ${qualifier.id ? `<div><dt>限定词 ID</dt><dd class="notranslate" translate="no">${this.escapeHTML(qualifier.id)}</dd></div>` : ''}
                    <div><dt>适用范围</dt><dd>${this.escapeHTML(qualifier.scope || 'event')}</dd></div>
                    ${qualifierTime ? `<div><dt>时间</dt><dd>${this.escapeHTML(qualifierTime)}</dd></div>` : ''}
                    ${by ? `<div><dt>表述主体</dt><dd class="event-chips">${by}</dd></div>` : ''}
                </dl></details></div>`;
        }).join('');
        const renderRelations = event => (event?.relations || []).map(relation => {
            const target = localIdByUuid[String(relation.target_event_uuid)]
                || relation.target_event_uuid || '未知事件';
            const targetEvent = events.find(candidate => eventKey(candidate) === String(relation.target_event_uuid));
            const targetTitle = this.predicateTitle(targetEvent?.predicate, '');
            const sourceTitle = this.predicateTitle(
                event?.predicate, event?.local_event_id || '当前事件');
            const targetUuid = String(relation.target_event_uuid || '');
            const trace = [
                targetUuid && targetUuid !== String(target) ? `目标 UUID：${targetUuid}` : '',
                relation.surface ? `原文：${relation.surface}` : ''
            ].filter(Boolean).join('；');
            return `<div class="event-relation-card"><span>${this.escapeHTML(sourceTitle)}</span><b>${this.escapeHTML(this.humanLabel(relation.predicate, relationLabels))}</b><span>${this.escapeHTML(targetTitle || target)}</span>${trace ? `<small>${this.escapeHTML(trace)}</small>` : ''}</div>`;
        }).join('');

        const eventSelector = events.map((event, index) => {
            const key = eventKey(event);
            const predicate = event?.predicate || {};
            const title = this.predicateTitle(predicate);
            const time = Object.values(event?.time || {})[0];
            return `<button type="button" class="event-sequence-node${key === primaryKey ? ' active' : ''}" data-event-select="${this.escapeHTML(key)}" aria-pressed="${key === primaryKey ? 'true' : 'false'}">
                <span><b>${event?.is_primary ? '主事件' : `相关事件 ${index + 1}`}</b></span>
                <strong>${this.escapeHTML(title)}</strong>
                <small>${time ? this.escapeHTML(time.normalized || time.surface || '') : '未提供明确时间'}</small>
            </button>`;
        }).join('');

        const detailPanels = events.map((event, index) => {
            const eventUuid = String(event?.event_uuid || event?.local_event_id || '');
            const isPrimary = Boolean(event?.is_primary);
            const predicate = event?.predicate || {};
            const predicateTitle = this.predicateTitle(predicate);
            const roles = renderRoles(event);
            const times = this.renderEventTime(event?.time);
            const locations = (event?.event_location || [])
                .map(value => this.renderEntityChip(value)).join('');
            const attributes = this.renderEventAttributes(event?.attributes);
            const qualifiers = renderQualifiers(event);
            const relations = renderRelations(event);
            const roleCount = Object.values(event?.roles || {}).flat().length;
            const firstTime = Object.values(event?.time || {})[0];
            const timeSummary = firstTime
                ? firstTime.normalized || firstTime.surface || String(firstTime) : '';
            const locationSummary = (event?.event_location || []).map(value =>
                value?.canonical_name || value?.name || String(value || '')).filter(Boolean).join('、');
            const roleItems = Object.entries(event?.roles || {}).flatMap(([role, values]) =>
                (values || []).map(value => ({
                    role,
                    semantic: event?.role_semantics?.[role] || 'other',
                    value
                })));
            const leadingGroups = new Set(['agent', 'source', 'authority']);
            let leadingItems = roleItems.filter(item => leadingGroups.has(item.semantic));
            if (!leadingItems.length && roleItems.length) leadingItems = [roleItems[0]];
            const leadingSet = new Set(leadingItems);
            const affectedItems = roleItems.filter(item => !leadingSet.has(item));
            const renderStoryParty = (items, fallback, label) => `<div class="event-story-party">
                <small>${label}</small>${items.length ? items.map(item => this.renderEntityChip(item.value)).join('') : `<span>${fallback}</span>`}
            </div>`;
            const storyline = `<div class="event-storyline">
                ${renderStoryParty(leadingItems, '行动方未标注', '谁在行动')}
                <div class="event-story-action"><span class="event-story-arrow before" aria-hidden="true">→</span><strong>${this.escapeHTML(predicateTitle)}</strong><small>${predicate.surface && predicate.surface !== predicateTitle ? `原文：${this.escapeHTML(predicate.surface)}` : (predicate.gloss ? this.escapeHTML(predicate.gloss) : '发生了什么')}</small><span class="event-story-arrow after" aria-hidden="true">→</span></div>
                ${renderStoryParty(affectedItems, '未指向特定对象', '影响谁 / 什么')}
            </div>`;
            const metadata = event?.metadata && Object.keys(event.metadata).length
                ? `<span><b>附加元数据：</b><code class="notranslate" translate="no">${this.escapeHTML(JSON.stringify(event.metadata))}</code></span>` : '';

            return `
                <article class="event-detail-panel${isPrimary ? ' primary' : ''}" data-event-panel="${this.escapeHTML(eventUuid)}" ${eventUuid === primaryKey ? '' : 'hidden'}>
                    <header class="event-detail-heading">
                        <div><span>${isPrimary ? '这篇报道的主事件' : `相关事件 ${index + 1}`}</span><h4>${this.escapeHTML(predicateTitle)}${predicate.surface && predicate.surface !== predicateTitle ? ` <small class="event-origin-text">原文：${this.escapeHTML(predicate.surface)}</small>` : ''}</h4></div>
                        <div class="event-story-context">${timeSummary ? `<span><i class="bi bi-clock"></i>${this.escapeHTML(timeSummary)}</span>` : ''}${locationSummary ? `<span><i class="bi bi-geo-alt"></i>事发地：${this.escapeHTML(locationSummary)}</span>` : ''}</div>
                    </header>
                    ${storyline}
                    <div class="event-detail-grid">
                        <div class="event-detail-main">
                            ${roles ? `<section class="event-detail-group"><h5>参与方分别扮演什么角色</h5><div class="event-role-grid">${roles}</div></section>` : ''}
                            ${(times || locations) ? `<section class="event-detail-group"><h5>事情发生在何时何地</h5>${times ? `<div class="event-facts">${times}</div>` : ''}${locations ? `<div class="event-location-row"><span>地点</span><div class="event-chips">${locations}</div></div>` : ''}</section>` : ''}
                            ${attributes ? `<section class="event-detail-group"><h5>报道中的关键数字</h5><div class="event-facts">${attributes}</div></section>` : ''}
                            ${qualifiers ? `<section class="event-detail-group"><h5>报道如何描述这件事</h5><div class="event-qualifier-list">${qualifiers}</div></section>` : ''}
                            ${relations ? `<section class="event-detail-group"><h5>它与其他事件有什么联系</h5><div class="event-relation-list">${relations}</div></section>` : ''}
                        </div>
                    </div>
                    <details class="event-technical">
                        <summary>查看结构化字段</summary><div class="event-technical-structure"><div class="event-frame-strip">${renderFrame(event)}${predicate.id ? `<code class="notranslate" translate="no">${this.escapeHTML(predicate.id)}</code>` : '<code>开放谓词</code>'}</div><span>${roleCount} 个实体引用 · ${Object.keys(event?.time || {}).length} 个时间表达 · ${(event?.qualifiers || []).length} 个限定词 · ${(event?.relations || []).length} 条关系</span></div><div class="event-technical-text"><span><b>局部编号：</b><span class="notranslate" translate="no">${this.escapeHTML(event.local_event_id || `E${index + 1}`)}</span></span>${eventUuid ? `<span><b>事件 UUID：</b><span class="notranslate" translate="no">${this.escapeHTML(eventUuid)}</span></span>` : ''}${event?.intelligence_uuid ? `<span><b>来源文章 UUID：</b><span class="notranslate" translate="no">${this.escapeHTML(event.intelligence_uuid)}</span></span>` : ''}${event?.observed_at ? `<span><b>系统观察时间：</b>${this.escapeHTML(this.anyTimeToTimeStr(event.observed_at))}</span>` : ''}${metadata}</div>
                    </details>
                </article>`;
        }).join('');

        const auditRows = events.map((event, index) => {
            const predicate = event?.predicate || {};
            const roles = Object.entries(event?.roles || {}).map(([role, values]) =>
                `<div><b>${this.escapeHTML(this.humanLabel(role, roleLabels))}</b>${(values || []).map(value => this.renderEntityChip(value)).join('')}</div>`).join('');
            const qualifiers = (event?.qualifiers || []).map(value =>
                [
                    value.id,
                    `${this.humanLabel(value.type, qualifierTypeLabels)}=${this.humanLabel(value.value, qualifierValueLabels)}`,
                    `范围=${value.scope || 'event'}`,
                    value.time ? `时间=${value.time.normalized || value.time.surface || ''}` : '',
                    (value.by || []).length ? `声明方=${value.by.map(item => item.canonical_name || item.name || '').filter(Boolean).join('/')}` : ''
                ].filter(Boolean).join(' · ')).join('；');
            const relations = (event?.relations || []).map(value => {
                const target = localIdByUuid[String(value.target_event_uuid)] || value.target_event_uuid || '?';
                return `${this.humanLabel(value.predicate, relationLabels)} → ${target}`;
            }).join('；');
            return `<tr><td><b>${this.escapeHTML(event.local_event_id || `E${index + 1}`)}</b>${event?.is_primary ? '<em>主事件</em>' : ''}<small>${this.escapeHTML(predicate.surface || predicate.gloss || '')}</small></td><td>${renderFrame(event)}${predicate.id ? `<code>${this.escapeHTML(predicate.id)}</code>` : ''}</td><td><div class="event-audit-roles">${roles || '<span>无</span>'}</div></td><td>${this.renderEventTime(event?.time) || '<span>无</span>'}<div class="event-chips">${(event?.event_location || []).map(value => this.renderEntityChip(value)).join('')}</div></td><td>${this.renderEventAttributes(event?.attributes) || '<span>无</span>'}</td><td>${this.escapeHTML(qualifiers || '无')}</td><td>${this.escapeHTML(relations || '无')}</td></tr>`;
        }).join('');

        const relationCards = events.map((event, index) => {
            const predicate = event?.predicate || {};
            return `<div class="event-map-node${event?.is_primary ? ' primary' : ''}"><span>${this.escapeHTML(event.local_event_id || `E${index + 1}`)}${event?.is_primary ? ' · 主事件' : ''}</span><b>${this.escapeHTML(this.predicateTitle(predicate))}</b><code>${this.escapeHTML(predicate.id || 'open')}</code></div>`;
        }).join('');
        const relationEdges = events.flatMap(event => (event?.relations || []).map(relation => {
            const target = localIdByUuid[String(relation.target_event_uuid)] || relation.target_event_uuid || '?';
            const targetEvent = events.find(candidate => eventKey(candidate) === String(relation.target_event_uuid));
            const sourceTitle = this.predicateTitle(event?.predicate, event?.local_event_id || '?');
            const targetTitle = this.predicateTitle(targetEvent?.predicate, targetEvent?.local_event_id || target);
            const trace = [
                `${event.local_event_id || '?'} → ${target}`,
                relation.surface || ''
            ].filter(Boolean).join(' · ');
            return `<div class="event-map-edge"><span>${this.escapeHTML(sourceTitle)}</span><b>${this.escapeHTML(this.humanLabel(relation.predicate, relationLabels))}</b><span>${this.escapeHTML(targetTitle)}</span><small>${this.escapeHTML(trace)}</small></div>`;
        })).join('');

        return `
            <section class="event-section" aria-labelledby="v4-event-heading">
                <div class="event-section-heading">
                    <div>
                        <h3 id="v4-event-heading"><i class="bi bi-diagram-3"></i> 事件结构</h3>
                        <p>先理解发生了什么；需要核验时，再展开全部字段。</p>
                    </div>
                    <div class="event-summary-counts"><span>${events.length} 个事件</span><span>${entityCount} 个实体</span>${extraction.schema_version ? `<code>${this.escapeHTML(extraction.schema_version)}</code>` : ''}</div>
                </div>
                <div class="event-notice"><i class="bi bi-info-circle"></i> 以下内容是对来源报道的结构化整理，不代表系统已独立核实相关事实。</div>
                <div class="event-view-tabs" role="tablist" aria-label="事件结构视图">
                    <button type="button" role="tab" class="active" data-event-view="narrative" aria-selected="true">事件解读</button>
                    <button type="button" role="tab" data-event-view="audit" aria-selected="false">全部信息</button>
                    <button type="button" role="tab" data-event-view="relations" aria-selected="false">事件联系</button>
                </div>
                <div class="event-view-panel" role="tabpanel" data-event-view-panel="narrative">
                    <nav class="event-sequence-rail" aria-label="事件序列">${eventSelector}</nav>
                    <div class="event-detail-list">${detailPanels}</div>
                </div>
                <div class="event-view-panel" role="tabpanel" data-event-view-panel="audit" hidden>
                    <div class="event-audit-table-wrap"><table class="event-audit-table"><thead><tr><th>事件</th><th>谓词与 Frame</th><th>角色</th><th>时间地点</th><th>属性</th><th>限定词</th><th>关系</th></tr></thead><tbody>${auditRows}</tbody></table></div>
                </div>
                <div class="event-view-panel" role="tabpanel" data-event-view-panel="relations" hidden>
                    <div class="event-map-nodes">${relationCards}</div>
                    <div class="event-map-edges">${relationEdges || '<p>当前事件之间没有显式关系。</p>'}</div>
                </div>
            </section>`;
    },

    // 统一生成 HTML 结构
    generateHTML: function(article) {
        // 先问插件：有整详情则由协议处理，否则回退默认实现
        const plugin = (window.DomainUI && window.DomainUI.getPlugin(window.IIS_DOMAIN || '')) || null;
        if (plugin) {
            const helpers = window.DomainUI.makeHelpers
                ? window.DomainUI.makeHelpers(this, window.IIS_BASE_PATH || '', window.IIS_DOMAIN || '')
                : { base: window.IIS_BASE_PATH || '', domain: window.IIS_DOMAIN || '' };
            const built = window.DomainUI.buildDetail(plugin, article, this, helpers);
            if (built && built.html) return built.html;
        }

        const uuid = this.escapeHTML(article?.UUID || '');
        const informant = article?.INFORMANT ? this.escapeHTML(article.INFORMANT) : '';
        const contentTime = this.anyTimeToTimeStr(
            article?.content_time || article?.archived_at || 'N/A');
        const contentTimeSource = article?.content_time_source
            || window.IntelligenceContract.ContentTimeSource.ARCHIVED;
        const contentTimeLabel = window.IntelligenceContract.contentTimeLabel(
            contentTimeSource, 'zh');
        const title = this.escapeHTML(article?.EVENT_TITLE || '无标题');
        const brief = this.escapeHTML(article?.EVENT_BRIEF || '暂无摘要');

        // 正文保持原文渲染，不做强制转义以支持排版。若需纯文本展示请加上 this.escapeHTML()
        const content = this.escapeHTML(article?.EVENT_TEXT || '暂无正文');
        const locations = (article?.LOCATION || []).map(value => this.escapeHTML(value));
        const people = (article?.PEOPLE || []).map(value => this.escapeHTML(value));
        const orgs = (article?.ORGANIZATION || []).map(value => this.escapeHTML(value));
        const times = (article?.TIME || []).map(value => this.anyTimeToTimeStr(value));
        const impact = this.escapeHTML(article?.IMPACT || '暂无影响分析');
        const tips = this.escapeHTML(article?.TIPS || '暂无分析建议');

        // [修复] 补全了 <a> 标签
        return `
        <div class="article-preview" id="article-root-${uuid}" data-uuid="${uuid}">
            <section class="header-box key-points">
                <div style="display:flex; flex-wrap:wrap; gap:8px; align-items:center; justify-content:space-between;">
                    <div style="min-width:0;">
                        <div style="font-size:12px; color:#555;">
                            <i class="bi bi-calendar-event"></i> ${contentTimeLabel}：${contentTime}
                            &nbsp;|&nbsp; <i class="bi bi-upc-scan"></i> <span class="notranslate" translate="no">${uuid}</span>
                        </div>
                        <h3 style="margin:6px 0 0; font-size:20px; line-height:1.35;">${title}</h3>
                        <div style="margin-top:6px; color:#333;">${brief}</div>
                    </div>
                    <div style="display:flex; gap:8px; align-items:center;">
                        ${informant ? `<a href="${informant}" target="_blank" class="btn btn-outline-secondary btn-sm"><i class="bi bi-link-45deg"></i> 来源</a>` : ''}
                        ${window.IIS_VECTOR_ENABLED === false ? '' : `<a href="${window.IIS_BASE_PATH || ''}/intelligences?search_mode=vector_similar&reference=${uuid}&score_threshold=0.6" class="btn btn-outline-primary btn-sm">
                            <i class="bi bi-intersect"></i> 查找相似情报
                        </a>`}
                    </div>
                </div>
            </section>

            <section class="meta-grid">
                <div class="meta-card">
                    <h5><i class="bi bi-geo-alt"></i> 相关地点</h5>
                    ${locations.length ? locations.join(', ') : '暂无地点信息'}
                </div>
                <div class="meta-card">
                    <h5><i class="bi bi-people"></i> 相关人物</h5>
                    ${people.length ? people.join(', ') : '暂无人物信息'}
                </div>
                <div class="meta-card">
                    <h5><i class="bi bi-building"></i> 相关组织</h5>
                    ${orgs.length ? orgs.join(', ') : '暂无组织信息'}
                </div>
            </section>

            <section class="meta-card" style="margin-top:10px;">
                <h5><i class="bi bi-clock-history"></i> 事件时间</h5>
                ${times.length ? times.join(', ') : '暂无明确时间'}
            </section>

            <section class="content-section" style="margin-top:16px;">
                ${content}
            </section>

            ${this.renderEventSection(article)}

            <section style="margin-top:16px;">
                ${this.renderRatingTable(article)}
            </section>

            <section class="impact-card" style="margin-top:16px;">
                <h5><i class="bi bi-lightning-charge"></i> 潜在影响</h5>
                <p>${impact}</p>
            </section>

            <section class="tip-card" style="margin-top:12px;">
                <h5><i class="bi bi-lightbulb"></i> 分析建议</h5>
                <p>${tips}</p>
            </section>
        </div>`;
    },

    // 绑定评分提交逻辑
    bindEvents: function(containerElement, uuid, toastContainerId) {
        containerElement.querySelectorAll('[data-event-view]').forEach(button => {
            button.addEventListener('click', () => {
                const section = button.closest('.event-section');
                if (!section) return;
                const view = button.dataset.eventView;
                section.querySelectorAll('[data-event-view]').forEach(item => {
                    const active = item === button;
                    item.classList.toggle('active', active);
                    item.setAttribute('aria-selected', active ? 'true' : 'false');
                });
                section.querySelectorAll('[data-event-view-panel]').forEach(panel => {
                    panel.hidden = panel.dataset.eventViewPanel !== view;
                });
            });
        });
        containerElement.querySelectorAll('[data-event-select]').forEach(button => {
            button.addEventListener('click', () => {
                const section = button.closest('.event-section');
                if (!section) return;
                const key = button.dataset.eventSelect;
                section.querySelectorAll('[data-event-select]').forEach(item => {
                    const active = item === button;
                    item.classList.toggle('active', active);
                    item.setAttribute('aria-pressed', active ? 'true' : 'false');
                });
                section.querySelectorAll('[data-event-panel]').forEach(panel => {
                    panel.hidden = panel.dataset.eventPanel !== key;
                });
            });
        });

        const submitBtn = containerElement.querySelector('#submit-rating');
        if (!submitBtn) return;

        submitBtn.addEventListener('click', async () => {
            const ratings = {};
            containerElement.querySelectorAll('input[id^="rating-"]').forEach(input => {
                const dim = input.id.replace('rating-', '');
                ratings[dim] = input.value;
            });

            try {
                const r = await fetch((window.IIS_BASE_PATH || '') + '/manual_rate', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        uuid: uuid,
                        ratings: ratings,
                        domain: window.IIS_DOMAIN || '',
                        timestamp: new Date().toISOString()
                    })
                });
                if (!r.ok) throw new Error(`HTTP ${r.status}`);
                this.showToast(toastContainerId, '评分提交成功', 'success');
            } catch (e) {
                this.showToast(toastContainerId, '评分提交失败：' + (e.message || e), 'danger');
            }
        });
    },

    showToast: function(containerId, message, type) {
        const container = document.getElementById(containerId);
        if (!container) return;
        const toast = document.createElement('div');
        toast.className = `article-toast ${type}`;
        toast.innerHTML = `<div style="flex:1;">${message}</div><button class="toast-close"><i class="bi bi-x-lg"></i></button>`;
        container.appendChild(toast);
        const remove = () => toast.remove();
        toast.querySelector('.toast-close').addEventListener('click', remove);
        setTimeout(remove, 2400);
    }
};
