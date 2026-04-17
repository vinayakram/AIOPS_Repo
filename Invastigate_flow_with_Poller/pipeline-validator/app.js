/* =====================================================
   Investigation Pipeline Validator — App Logic
   Mirrors the 5-step orchestrator flow:
   Normalization → Correlation → Analysis → RCA → Recommendations
   ===================================================== */

const STEPS = [
  { id: 'normalization',   label: 'Normalization',   num: '1', endpoint: '/api/v1/normalize' },
  { id: 'correlation',     label: 'Correlation',     num: '2', endpoint: '/api/v1/correlate' },
  { id: 'analysis',        label: 'Analysis',        num: '3', endpoint: '/api/v1/error-analysis' },
  { id: 'rca',             label: 'RCA',             num: '4', endpoint: '/api/v1/rca' },
  { id: 'recommendations', label: 'Recommendations', num: '5', endpoint: '/api/v1/recommend' },
];

const SCENARIOS = [
  {
    id: 'ai_agent_error',
    name: 'AI Agent Error',
    desc: 'LLM access disabled in demo mode',
    data: {
      traceId: '8e3d196e9bda4fd09a4833740db8d360',
      agentName: 'medical-rag',
      timestamp: '2026-04-10T04:19:55.204Z',
      expectedError: 'AI_AGENT',
    }
  },
  {
    id: 'no_error',
    name: 'Clean trace',
    desc: 'No error — stops at normalization',
    data: {
      traceId: 'e7fa7bf5e6b047d1829eb0cfd09c8ca6',
      agentName: 'medical-rag',
      timestamp: '2026-04-10 09:49:55',
      expectedError: 'NO_ERROR',
    }
  },
  {
    id: 'infra',
    name: 'Infrastructure',
    desc: 'Database / network failure scenario',
    data: {
      traceId: 'aaa111bbb222ccc333ddd444',
      agentName: 'data-service',
      timestamp: new Date().toISOString(),
      expectedError: 'INFRASTRUCTURE',
    }
  },
  {
    id: 'custom',
    name: 'Custom input',
    desc: 'Use fields on the left',
    data: null
  }
];

/* ── Application State ── */
let state = {
  currentStep: -1,
  stepResults: {},
  stepStatuses: {},   // pending | running | completed | failed | skipped
  activeTab: {},
  activeDetailStep: 0,
  running: false,
  totalTime: 0,
};

/* ── Bootstrap ── */
function init() {
  renderStepBar();
  renderScenarios();
  selectScenario('ai_agent_error');
}

/* ── Render pipeline progress bar ── */
function renderStepBar() {
  const bar = document.getElementById('pipeline-bar');
  bar.innerHTML = STEPS.map((s, i) => {
    const st = state.stepStatuses[s.id] || 'pending';
    const isActive = state.activeDetailStep === i;
    const cls = isActive ? 'active' : (st === 'completed' ? 'done' : st === 'failed' ? 'failed' : '');
    return `
      <div class="step-item ${cls}" onclick="showStepDetail(${i})">
        ${i > 0 ? '<div class="step-arrow"></div>' : ''}
        <div class="step-inner">
          <div class="step-num">${st === 'completed' ? '✓' : st === 'failed' ? '✗' : s.num}</div>
          <div class="step-label">${s.label}</div>
        </div>
      </div>
    `;
  }).join('');
}

/* ── Render scenario quick-select grid ── */
function renderScenarios() {
  const grid = document.getElementById('scenario-grid');
  grid.innerHTML = SCENARIOS.map(s => `
    <div class="scenario-card" id="sc-${s.id}" onclick="selectScenario('${s.id}')">
      <div class="scenario-name">${s.name}</div>
      <div class="scenario-desc">${s.desc}</div>
    </div>
  `).join('');
}

/* ── Select a preset scenario ── */
function selectScenario(id) {
  document.querySelectorAll('.scenario-card').forEach(c => c.classList.remove('active'));
  document.getElementById('sc-' + id)?.classList.add('active');
  const sc = SCENARIOS.find(s => s.id === id);
  if (sc?.data) {
    document.getElementById('trace-id').value = sc.data.traceId;
    document.getElementById('agent-name').value = sc.data.agentName;
    document.getElementById('timestamp').value = sc.data.timestamp;
    document.getElementById('expected-error').value = sc.data.expectedError;
  }
}

/* ── Show detail for a specific step ── */
function showStepDetail(idx) {
  state.activeDetailStep = idx;
  renderStepBar();
  renderDetailPanel();
}

/* ── Render the right-side detail panel ── */
function renderDetailPanel() {
  const panel = document.getElementById('detail-panel');
  const s = STEPS[state.activeDetailStep];
  const status = state.stepStatuses[s.id] || 'pending';
  const result = state.stepResults[s.id];

  const statusLabel = {
    pending: 'Pending',
    running: 'Running...',
    completed: 'Completed',
    failed: 'Failed',
    skipped: 'Skipped (NO_ERROR)'
  }[status] || status;

  const statusClass = `status-${status}`;

  // Metrics row (only when result available)
  let metricsHtml = '';
  if (result) {
    const ms = result._time_ms || '--';
    const logs = result.output?.raw_log_count || result.output?.total_logs_analyzed || '--';
    const conf = extractConfidence(result.output);
    metricsHtml = `
      <div class="metrics-row">
        <div class="metric-card">
          <div class="metric-label">Processing time</div>
          <div class="metric-value">${typeof ms === 'number' ? Math.round(ms) : ms}</div>
          <div class="metric-sub">ms</div>
        </div>
        <div class="metric-card">
          <div class="metric-label">Logs analyzed</div>
          <div class="metric-value">${logs}</div>
          <div class="metric-sub">records</div>
        </div>
        <div class="metric-card">
          <div class="metric-label">Confidence</div>
          <div class="metric-value">${conf}</div>
          <div class="metric-sub">score</div>
        </div>
      </div>
    `;
  }

  const validations = getValidations(s.id, status, result);
  const validHtml = validations.length ? `
    <div class="validations">
      ${validations.map(v => `
        <div class="validation-item">
          <div class="v-icon ${v.type === 'pass' ? 'v-pass' : v.type === 'fail' ? 'v-fail' : v.type === 'warn' ? 'v-warn' : 'v-info'}">
            ${v.type === 'pass' ? '✓' : v.type === 'fail' ? '✗' : v.type === 'warn' ? '!' : 'i'}
          </div>
          <div class="v-content">
            <div class="v-title">${escapeHtml(v.title)}</div>
            <div class="v-desc">${escapeHtml(v.desc)}</div>
          </div>
        </div>
      `).join('')}
    </div>
  ` : '';

  const ioHtml = result
    ? renderIO(s.id, result)
    : (status === 'pending' ? `
        <div class="empty-state">
          <p>This step has not run yet.</p>
          <p style="margin-top:4px; font-size:12px">Run the pipeline to see input / output.</p>
        </div>
      ` : '');

  panel.innerHTML = `
    <div class="step-header">
      <div class="step-title">
        <span style="font-size:12px; color: var(--text3); font-family: var(--mono)">Step ${s.num}</span>
        ${escapeHtml(s.label)}
      </div>
      <span class="status-badge ${statusClass} ${status === 'running' ? 'pulsing' : ''}">${statusLabel}</span>
    </div>
    ${metricsHtml}
    ${validHtml}
    ${validations.length && ioHtml ? '<div class="divider"></div>' : ''}
    ${ioHtml}
  `;
}

/* ── Render Input / Output tabs ── */
function renderIO(stepId, result) {
  const activeTab = state.activeTab[stepId] || 'input';
  return `
    <div class="tabs">
      <div class="tab ${activeTab === 'input'  ? 'active' : ''}" onclick="switchTab('${stepId}','input')">Input</div>
      <div class="tab ${activeTab === 'output' ? 'active' : ''}" onclick="switchTab('${stepId}','output')">Output</div>
      <div class="tab ${activeTab === 'raw'    ? 'active' : ''}" onclick="switchTab('${stepId}','raw')">Raw JSON</div>
    </div>
    <div id="tab-content-${stepId}">
      ${renderTabContent(stepId, activeTab, result)}
    </div>
  `;
}

function switchTab(stepId, tab) {
  state.activeTab[stepId] = tab;
  const r = state.stepResults[stepId];
  const el = document.getElementById('tab-content-' + stepId);
  if (el) el.innerHTML = renderTabContent(stepId, tab, r);
}

function renderTabContent(stepId, tab, result) {
  if (!result) return '';
  const step = STEPS.find(s => s.id === stepId);

  if (tab === 'input') {
    return `<div class="io-block">
      <div class="io-header">
        <span class="io-label">Request payload</span>
        <span class="io-time">→ ${step.endpoint}</span>
      </div>
      <pre>${escapeHtml(JSON.stringify(result.input || {}, null, 2))}</pre>
    </div>`;
  }

  if (tab === 'output') {
    return renderStructuredOutput(stepId, result.output);
  }

  if (tab === 'raw') {
    return `<div class="io-block">
      <div class="io-header">
        <span class="io-label">Full response</span>
        <span class="io-time">${result._time_ms ? Math.round(result._time_ms) + ' ms' : ''}</span>
      </div>
      <pre>${escapeHtml(JSON.stringify(result.output || {}, null, 2))}</pre>
    </div>`;
  }
}

function renderStructuredOutput(stepId, output) {
  if (!output) return '<div class="empty-state"><p>No output data</p></div>';
  const items = getKeyOutputItems(stepId, output);
  if (!items.length) {
    return `<div class="io-block"><pre>${escapeHtml(JSON.stringify(output, null, 2))}</pre></div>`;
  }
  return items.map(item => `
    <div class="io-block">
      <div class="io-header"><span class="io-label">${escapeHtml(item.label)}</span></div>
      <pre>${escapeHtml(typeof item.value === 'string' ? item.value : JSON.stringify(item.value, null, 2))}</pre>
    </div>
  `).join('');
}

/* ── Extract key fields per agent for structured view ── */
function getKeyOutputItems(stepId, output) {
  if (stepId === 'normalization') {
    const inc = output.incident || output;
    return [
      { label: 'error type',     value: inc.error_type || '--' },
      { label: 'error summary',  value: inc.error_summary || '--' },
      { label: 'confidence',     value: inc.confidence !== undefined ? (inc.confidence * 100).toFixed(0) + '%' : '--' },
      { label: 'signals detected', value: (inc.signals || []).join(', ') || 'none' },
    ].filter(i => i.value && i.value !== '--');
  }
  if (stepId === 'correlation') {
    const corr = output.correlation || output;
    return [
      { label: 'analysis target',       value: corr.analysis_target || '--' },
      { label: 'root cause candidate',  value: corr.root_cause_candidate || '--' },
      { label: 'correlation chain',     value: corr.correlation_chain || [] },
      { label: 'timeline',              value: corr.timeline || [] },
    ].filter(i => i.value && i.value !== '--');
  }
  if (stepId === 'analysis') {
    const a = output.analysis || output;
    return [
      { label: 'summary',       value: a.analysis_summary || '--' },
      { label: 'errors found',  value: a.errors || [] },
      { label: 'error impacts', value: a.error_impacts || [] },
    ].filter(i => i.value && i.value !== '--');
  }
  if (stepId === 'rca') {
    const r = output.rca || output;
    const items = [
      { label: 'rca summary',        value: r.rca_summary || '--' },
      { label: 'root cause',         value: r.root_cause || '--' },
      { label: 'blast radius',       value: (r.blast_radius || []).join(', ') || 'none' },
      { label: 'causal chain',       value: r.causal_chain || [] },
    ];
    if (r.five_why_analysis) {
      items.push({ label: 'five why — problem',        value: r.five_why_analysis.problem_statement || '--' });
      (r.five_why_analysis.whys || []).forEach(w => {
        items.push({ label: `why ${w.step} — ${w.component}`, value: `Q: ${w.question}\nA: ${w.answer}\nEvidence: ${w.evidence}` });
      });
      items.push({ label: 'fundamental root cause',   value: r.five_why_analysis.fundamental_root_cause || '--' });
    }
    return items.filter(i => i.value && i.value !== '--');
  }
  if (stepId === 'recommendations') {
    const rec = output.recommendations || output;
    return [
      { label: 'summary',   value: rec.recommendation_summary || '--' },
      { label: 'solutions', value: rec.solutions || [] },
    ].filter(i => i.value && i.value !== '--');
  }
  return [];
}

/* ── Per-step validation checks ── */
function getValidations(stepId, status, result) {
  if (status === 'pending') return [];
  if (status === 'running') return [{ type: 'info', title: 'Running...', desc: 'Waiting for response from the agent.' }];
  if (status === 'failed')  return [{ type: 'fail', title: 'Step failed', desc: result?.error || 'The agent returned an error or the request timed out.' }];
  if (status === 'skipped') return [{ type: 'warn', title: 'Skipped', desc: 'Normalization detected NO_ERROR — pipeline short-circuited as designed.' }];

  const output = result?.output;
  if (!output) return [{ type: 'fail', title: 'No output', desc: 'Agent responded but returned no data.' }];

  const v = [];

  if (stepId === 'normalization') {
    const inc = output.incident || output;
    const expected = document.getElementById('expected-error')?.value;
    v.push({ type: inc.error_type ? 'pass' : 'fail', title: 'error_type present', desc: `Value: ${inc.error_type || 'missing'}` });
    if (expected && inc.error_type) {
      v.push(inc.error_type === expected
        ? { type: 'pass', title: 'Error type matches expected', desc: `Got "${inc.error_type}" as expected` }
        : { type: 'warn', title: 'Error type differs from expected', desc: `Expected "${expected}", got "${inc.error_type}"` });
    }
    v.push({ type: inc.confidence !== undefined ? 'pass' : 'fail', title: 'confidence score valid', desc: inc.confidence !== undefined ? `${(inc.confidence * 100).toFixed(0)}% (range 0–1)` : 'missing' });
    v.push({ type: inc.timestamp ? 'pass' : 'warn', title: 'timestamp present', desc: inc.timestamp || 'missing' });
    v.push({ type: inc.entities ? 'pass' : 'warn', title: 'entities block present', desc: JSON.stringify(inc.entities || {}) });
  }

  if (stepId === 'correlation') {
    const corr = output.correlation || output;
    v.push({ type: corr.correlation_chain?.length > 0 ? 'pass' : 'warn', title: 'correlation chain built', desc: `${corr.correlation_chain?.length || 0} events in chain` });
    v.push({ type: corr.root_cause_candidate ? 'pass' : 'warn', title: 'root cause candidate identified', desc: corr.root_cause_candidate?.component || 'not identified' });
    v.push({ type: corr.analysis_target ? 'pass' : 'warn', title: 'analysis_target set', desc: corr.analysis_target || 'missing' });
    v.push({ type: output.total_logs_analyzed > 0 ? 'pass' : 'warn', title: 'logs were analyzed', desc: `${output.total_logs_analyzed || 0} logs from sources: ${(output.data_sources || []).join(', ')}` });
  }

  if (stepId === 'analysis') {
    const a = output.analysis || output;
    v.push({ type: a.errors?.length > 0 ? 'pass' : 'warn', title: 'errors extracted', desc: `${a.errors?.length || 0} error(s) found` });
    v.push({ type: a.analysis_summary ? 'pass' : 'fail', title: 'summary generated', desc: a.analysis_summary ? a.analysis_summary.slice(0, 80) + '…' : 'missing' });
    v.push({ type: a.confidence !== undefined ? 'pass' : 'warn', title: 'confidence score', desc: a.confidence !== undefined ? `${(a.confidence * 100).toFixed(0)}%` : 'missing' });
    v.push({ type: a.error_impacts?.length > 0 ? 'pass' : 'info', title: 'impact assessment', desc: `${a.error_impacts?.length || 0} impacted component(s)` });
  }

  if (stepId === 'rca') {
    const r = output.rca || output;
    v.push({ type: r.root_cause ? 'pass' : 'fail', title: 'root cause identified', desc: r.root_cause?.component || 'not found' });
    v.push({ type: r.rca_summary ? 'pass' : 'fail', title: 'rca summary generated', desc: r.rca_summary ? r.rca_summary.slice(0, 80) + '…' : 'missing' });
    v.push({ type: r.causal_chain?.length > 0 ? 'pass' : 'warn', title: 'causal chain built', desc: `${r.causal_chain?.length || 0} step(s) in chain` });
    v.push({ type: r.confidence !== undefined ? 'pass' : 'warn', title: 'confidence score', desc: r.confidence !== undefined ? `${(r.confidence * 100).toFixed(0)}%` : 'missing' });
    v.push({ type: r.blast_radius?.length > 0 ? 'pass' : 'info', title: 'blast radius defined', desc: (r.blast_radius || []).join(', ') || 'empty' });
    // Five Whys validation
    const fwa = r.five_why_analysis;
    v.push({ type: fwa ? 'pass' : 'fail', title: 'five why analysis present', desc: fwa ? 'five_why_analysis block found' : 'missing' });
    if (fwa) {
      const whyCount = fwa.whys?.length || 0;
      v.push({ type: whyCount === 5 ? 'pass' : 'fail', title: 'five why steps complete', desc: `${whyCount}/5 why step(s) present` });
      v.push({ type: fwa.problem_statement ? 'pass' : 'warn', title: 'problem statement defined', desc: fwa.problem_statement ? fwa.problem_statement.slice(0, 80) + '…' : 'missing' });
      v.push({ type: fwa.fundamental_root_cause ? 'pass' : 'warn', title: 'fundamental root cause stated', desc: fwa.fundamental_root_cause ? fwa.fundamental_root_cause.slice(0, 80) + '…' : 'missing' });
    }
  }

  if (stepId === 'recommendations') {
    const rec = output.recommendations || output;
    v.push({ type: rec.solutions?.length > 0 ? 'pass' : 'fail', title: 'solutions generated', desc: `${rec.solutions?.length || 0} solution(s)` });
    v.push({ type: rec.recommendation_summary ? 'pass' : 'fail', title: 'summary present', desc: rec.recommendation_summary ? rec.recommendation_summary.slice(0, 80) + '…' : 'missing' });
    const rcSolution = rec.solutions?.find(s => s.addresses_root_cause);
    v.push({ type: rcSolution ? 'pass' : 'warn', title: 'addresses root cause', desc: rcSolution ? `"${rcSolution.title}"` : 'no solution marked as addressing root cause' });
    const quickFix = rec.solutions?.find(s => s.effort === 'quick_fix');
    if (quickFix) v.push({ type: 'info', title: 'quick fix available', desc: `"${quickFix.title}"` });
  }

  return v;
}

/* ── Update summary bar ── */
function updateSummaryBar() {
  const bar = document.getElementById('summary-bar');
  const completed = Object.values(state.stepStatuses).filter(s => s === 'completed').length;
  const failed    = Object.values(state.stepStatuses).filter(s => s === 'failed').length;
  const skipped   = Object.values(state.stepStatuses).filter(s => s === 'skipped').length;

  if (completed + failed + skipped === 0) { bar.style.display = 'none'; return; }
  bar.style.display = 'flex';

  let passCount = 0, failCount = 0, warnCount = 0;
  Object.keys(state.stepResults).forEach(stepId => {
    const r  = state.stepResults[stepId];
    const st = state.stepStatuses[stepId];
    if (st === 'completed') {
      const vv = getValidations(stepId, st, r);
      passCount += vv.filter(x => x.type === 'pass').length;
      failCount += vv.filter(x => x.type === 'fail').length;
      warnCount += vv.filter(x => x.type === 'warn').length;
    }
  });

  bar.innerHTML = `
    <span style="font-weight:500; color: var(--text);">Pipeline summary</span>
    <span class="summary-stat"><span class="dot dot-green"></span>${completed} completed</span>
    ${failed  > 0 ? `<span class="summary-stat"><span class="dot dot-red"></span>${failed} failed</span>` : ''}
    ${skipped > 0 ? `<span class="summary-stat"><span class="dot dot-amber"></span>${skipped} skipped</span>` : ''}
    <span style="color: var(--border2)">|</span>
    <span class="summary-stat"><span class="dot dot-green"></span>${passCount} checks passed</span>
    ${failCount > 0 ? `<span class="summary-stat"><span class="dot dot-red"></span>${failCount} checks failed</span>` : ''}
    ${warnCount > 0 ? `<span class="summary-stat"><span class="dot dot-amber"></span>${warnCount} warnings</span>` : ''}
    ${state.totalTime > 0 ? `<span style="margin-left:auto;font-family:var(--mono);font-size:11px;color:var(--text3)">${(state.totalTime / 1000).toFixed(1)}s total</span>` : ''}
  `;
}

/* ── Mock data (used when real API is unreachable) ── */
function getMockResult(stepId, input) {
  const delay = Math.floor(Math.random() * 1500) + 400;
  const traceId   = input.trace_id || input.traceId;
  const agentName = input.agent_name || input.agentName;
  const isNoError = document.getElementById('expected-error').value === 'NO_ERROR';

  const mockOutputs = {
    normalization: isNoError ? {
      incident: {
        error_type: 'NO_ERROR',
        error_summary: 'No error detected',
        confidence: 1.0,
        timestamp: input.timestamp,
        entities: { agent_id: agentName, trace_id: traceId },
        signals: []
      },
      data_source: 'langfuse',
      raw_log_count: 8,
      processing_time_ms: delay
    } : {
      incident: {
        error_type: document.getElementById('expected-error').value || 'AI_AGENT',
        error_summary: `LLM access is disabled in '${agentName}' service due to demo error mode.`,
        confidence: 1.0,
        timestamp: input.timestamp,
        entities: { agent_id: agentName, service: agentName, trace_id: traceId },
        signals: ['LLM_access_disabled']
      },
      data_source: 'langfuse',
      raw_log_count: 7,
      processing_time_ms: delay
    },

    correlation: {
      correlation: {
        correlation_chain: [`LLM access is disabled in '${agentName}' due to demo error mode`],
        peer_components: [],
        timeline: [{ timestamp: input.timestamp, event: 'LLM access disabled (demo error mode).', service: agentName }],
        root_cause_candidate: { component: agentName, confidence: 1.0, reason: 'Error explicitly stated as LLM access being disabled.' },
        analysis_target: 'Agent'
      },
      data_sources: ['prometheus', 'langfuse'],
      total_logs_analyzed: 13,
      processing_time_ms: delay
    },

    analysis: {
      analysis: {
        analysis_summary: `The '${agentName}' service experienced a configuration error where LLM access was disabled due to demo error mode.`,
        analysis_target: 'Agent',
        errors: [{
          error_id: 'ERR-001',
          category: 'configuration_error',
          severity: 'high',
          component: agentName,
          error_message: 'LLM access is disabled (demo error mode).',
          timestamp: input.timestamp,
          source: 'langfuse'
        }],
        error_patterns: [],
        error_impacts: [{ affected_service: agentName, impact_description: 'Unable to process LLM queries.', severity: 'high' }],
        error_propagation_path: [],
        confidence: 1.0
      },
      rca_target: 'Agent',
      data_sources: ['langfuse'],
      total_logs_analyzed: 7,
      processing_time_ms: delay
    },

    rca: {
      rca: {
        rca_summary: `The root cause was a configuration error in '${agentName}' where LLM access was disabled due to demo error mode.`,
        root_cause: {
          category: 'configuration',
          component: agentName,
          description: `'${agentName}' was set to demo error mode, disabling LLM access.`,
          evidence: ['LLM access is disabled (demo error mode). Click Enable LLM Access in the chat UI to restore.'],
          error_ids: ['ERR-001'],
          confidence: 1.0
        },
        causal_chain: [
          { source_event: 'Service set to demo error mode', target_event: 'LLM access disabled', link_type: 'direct_cause', evidence: 'Log: LLM access is disabled (demo error mode).' },
          { source_event: 'LLM access disabled', target_event: 'Service unable to process queries', link_type: 'direct_cause', evidence: 'Agent returned error output with zero tokens processed.' }
        ],
        contributing_factors: [],
        failure_timeline: [{ timestamp: input.timestamp, component: agentName, event: 'LLM access disabled due to demo error mode', is_root_cause: true }],
        blast_radius: [agentName],
        five_why_analysis: {
          problem_statement: `'${agentName}' failed to process requests — LLM access is disabled.`,
          whys: [
            {
              step: 1,
              question: `Why did '${agentName}' fail to process requests?`,
              answer: 'The service returned an error stating LLM access is disabled.',
              evidence: 'Log: LLM access is disabled (demo error mode). Click Enable LLM Access in the chat UI to restore.',
              component: agentName
            },
            {
              step: 2,
              question: 'Why was LLM access disabled?',
              answer: 'The service was configured to run in demo error mode, which programmatically disables LLM access.',
              evidence: 'Error message explicitly references "demo error mode" as the reason for disablement.',
              component: agentName
            },
            {
              step: 3,
              question: 'Why was the service set to demo error mode?',
              answer: 'A configuration flag activating demo error mode was toggled — either manually via the chat UI or through a deployment config.',
              evidence: 'The error message instructs the user to click "Enable LLM Access" in the chat UI, indicating a UI-level toggle controls this state.',
              component: agentName
            },
            {
              step: 4,
              question: 'Why was the demo error mode flag not detected or prevented before processing started?',
              answer: 'There is no startup health check or pre-flight validation that guards against LLM access being disabled before requests are accepted.',
              evidence: 'Service accepted the request and only failed at the LLM call stage — no early-rejection guard observed in logs.',
              component: agentName
            },
            {
              step: 5,
              question: 'Why is there no pre-flight validation for LLM access state?',
              answer: 'The demo error mode feature was added for testing purposes without a corresponding readiness gate, leaving production traffic exposed to the disabled state.',
              evidence: 'Limited visibility — inferred from the absence of any startup or readiness log entries rejecting demo error mode.',
              component: agentName
            }
          ],
          fundamental_root_cause: 'The demo error mode feature lacks a readiness/health gate, allowing the service to accept live traffic while LLM access is disabled — the configuration toggle was never paired with an enforcement mechanism.'
        },
        confidence: 1.0
      },
      rca_target: 'Agent',
      data_sources: ['langfuse'],
      total_logs_analyzed: 7,
      processing_time_ms: delay
    },

    recommendations: {
      recommendations: {
        recommendation_summary: `Reconfigure '${agentName}' to disable demo error mode and restore LLM access.`,
        solutions: [{
          rank: 1,
          title: `Disable Demo Error Mode in '${agentName}'`,
          description: 'Reconfigure to disable demo error mode and enable LLM access.',
          category: 'config_change',
          effort: 'quick_fix',
          addresses_root_cause: true,
          affected_components: [agentName],
          expected_outcome: 'Service regains LLM access and full functionality.',
          error_ids: ['ERR-001']
        }],
        prevention_measures: [{ title: 'Add config validation', description: 'Validate LLM config at startup.', category: 'process' }]
      },
      data_sources: ['langfuse'],
      total_logs_analyzed: 7,
      processing_time_ms: delay
    }
  };

  return new Promise(resolve => {
    setTimeout(() => resolve({ output: mockOutputs[stepId], input, _time_ms: delay }), delay);
  });
}

/* ── Try real FastAPI endpoint first ── */
async function tryRealApi(endpoint, payload) {
  const base = document.getElementById('api-url').value.trim().replace(/\/$/, '');
  try {
    const r = await fetch(base + endpoint, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: AbortSignal.timeout(30000)
    });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return await r.json();
  } catch {
    return null;  // Fall back to mock
  }
}

/* ── Run full pipeline ── */
async function runPipeline() {
  if (state.running) return;
  resetAll(false);
  state.running = true;
  const t0 = Date.now();

  for (let i = 0; i < STEPS.length; i++) {
    state.activeDetailStep = i;
    const s = STEPS[i];
    state.stepStatuses[s.id] = 'running';
    renderStepBar();
    renderDetailPanel();

    await runSingleStep(i);

    // NO_ERROR short-circuit — mirrors orchestrator behaviour
    if (s.id === 'normalization' && state.stepStatuses['normalization'] === 'completed') {
      const out = state.stepResults['normalization']?.output;
      const inc = out?.incident || out;
      if (inc?.error_type === 'NO_ERROR') {
        for (let j = i + 1; j < STEPS.length; j++) {
          state.stepStatuses[STEPS[j].id] = 'skipped';
        }
        state.activeDetailStep = 0;
        break;
      }
    }

    if (state.stepStatuses[s.id] === 'failed') break;
  }

  state.running = false;
  state.totalTime = Date.now() - t0;
  renderStepBar();
  renderDetailPanel();
  updateSummaryBar();
}

/* ── Advance one step at a time ── */
async function runStep() {
  if (state.running) return;
  const nextIdx = STEPS.findIndex(s => !state.stepStatuses[s.id] || state.stepStatuses[s.id] === 'pending');
  if (nextIdx === -1) { resetAll(false); return; }

  state.running = true;
  state.activeDetailStep = nextIdx;
  state.stepStatuses[STEPS[nextIdx].id] = 'running';
  renderStepBar();
  renderDetailPanel();

  await runSingleStep(nextIdx);

  state.running = false;
  renderStepBar();
  renderDetailPanel();
  updateSummaryBar();
}

/* ── Execute one step (real API → fallback mock) ── */
async function runSingleStep(idx) {
  const s = STEPS[idx];
  const input = buildInput(idx);
  try {
    const real = await tryRealApi(s.endpoint, input);
    const result = real
      ? { output: real, input, _time_ms: real.processing_time_ms || 0 }
      : await getMockResult(s.id, input);
    state.stepResults[s.id] = result;
    state.stepStatuses[s.id] = 'completed';
  } catch (e) {
    state.stepResults[s.id] = { output: null, input, error: e.message };
    state.stepStatuses[s.id] = 'failed';
  }
}

/* ── Build request payload for each step (chaining outputs) ── */
function buildInput(idx) {
  const traceId   = document.getElementById('trace-id').value.trim();
  const agentName = document.getElementById('agent-name').value.trim();
  const ts        = document.getElementById('timestamp').value.trim();
  const base      = { trace_id: traceId, agent_name: agentName, timestamp: ts };

  if (idx === 0) return base;

  if (idx === 1) {
    const norm = state.stepResults['normalization']?.output;
    return { ...base, incident: norm?.incident || norm };
  }
  if (idx === 2) {
    const corr = state.stepResults['correlation']?.output;
    const norm = state.stepResults['normalization']?.output;
    return { ...base, correlation: corr?.correlation || corr, incident: norm?.incident || norm };
  }
  if (idx === 3) {
    const ea   = state.stepResults['analysis']?.output;
    const norm = state.stepResults['normalization']?.output;
    return {
      ...base,
      error_analysis: ea?.analysis || ea,
      rca_target: ea?.rca_target,          // required by RCARequest — routed from error analysis
      incident: norm?.incident || norm,
    };
  }
  if (idx === 4) {
    const ea  = state.stepResults['analysis']?.output;
    const rca = state.stepResults['rca']?.output;
    // rca?.rca is the RCAResult object; must include five_why_analysis (required field)
    return { ...base, error_analysis: ea?.analysis || ea, rca: rca?.rca || rca };
  }
  return base;
}

/* ── Reset all state ── */
function resetAll(render = true) {
  state = {
    ...state,
    currentStep: -1,
    stepResults: {},
    stepStatuses: {},
    activeTab: {},
    activeDetailStep: 0,
    running: false,
    totalTime: 0
  };
  if (render) {
    renderStepBar();
    document.getElementById('detail-panel').innerHTML = `
      <div class="empty-state">
        <div class="empty-icon">⚗</div>
        <p style="font-weight: 500; color: var(--text2); margin-bottom: 4px;">No pipeline run yet</p>
        <p>Select a scenario or enter a trace ID<br>and click "Run full pipeline"</p>
      </div>`;
    document.getElementById('summary-bar').style.display = 'none';
  }
}

/* ── Check poller health ── */
async function checkPoller() {
  const base = document.getElementById('api-url').value.trim().replace(/\/$/, '');
  const el   = document.getElementById('poller-status');
  el.innerHTML = '<div class="pulsing" style="font-size:12px;color:var(--text2)">Checking...</div>';
  try {
    const r = await fetch(base + '/api/v1/poller/status', { signal: AbortSignal.timeout(5000) });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const d = await r.json();
    el.innerHTML = `<pre style="font-size:11px;padding:0;max-height:120px">${escapeHtml(JSON.stringify(d, null, 2))}</pre>`;
  } catch {
    el.innerHTML = `
      <div style="font-size:12px;color:var(--warn-text);">Could not reach ${escapeHtml(base)}</div>
      <div style="font-size:11px;color:var(--text3);margin-top:4px;">Start the server: <code style="font-family:var(--mono)">uvicorn app.main:app --reload</code></div>
      <div style="font-size:11px;color:var(--text3);margin-top:4px;">Running in demo mode with mock data.</div>`;
  }
}

/* ── Helpers ── */
function extractConfidence(output) {
  if (!output) return '--';
  if (output.incident?.confidence !== undefined)
    return (output.incident.confidence * 100).toFixed(0) + '%';
  if (output.correlation?.root_cause_candidate?.confidence !== undefined)
    return (output.correlation.root_cause_candidate.confidence * 100).toFixed(0) + '%';
  if (output.analysis?.confidence !== undefined)
    return (output.analysis.confidence * 100).toFixed(0) + '%';
  if (output.rca?.confidence !== undefined)
    return (output.rca.confidence * 100).toFixed(0) + '%';
  return '--';
}

function escapeHtml(str) {
  if (typeof str !== 'string') return String(str ?? '');
  return str
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/* ── Start ── */
init();
