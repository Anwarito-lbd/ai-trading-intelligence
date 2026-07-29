import { FormEvent, useCallback, useEffect, useMemo, useState } from 'react'
import { API_BASE, useLanguage } from './appShared'

type IndicatorRow = {
  indicator_id: string
  name: string
  side: string
  strength: number
  weight: number
  fresh: boolean
}

type Decision = {
  trade_number: number
  trade_label: string
  symbol: string
  timeframe: string
  decision: string
  signal_label?: string
  show_signal?: boolean
  good_trade?: boolean
  signal_quality?: { quality_score?: number; reasons?: string[]; label?: string; suppressed?: string }
  technical_score: number
  ai_confidence: number
  news_score: number
  macro_bias: string
  geopolitical_risk: string
  analysts: Record<string, { bias?: string; score?: number }>
  bull_research: number
  bear_research: number
  trader: string
  risk: { status?: string; approved?: boolean; rr?: number; reasons?: string[] }
  entry?: number
  sl?: number
  tps?: number[]
  quantity?: number
  paper_status?: string
  created_at?: string
  pine?: { indicators?: IndicatorRow[]; direction?: string }
  pip_plan?: {
    message?: string
    stop_pips?: number
    tp1_pips?: number
    tp2_pips?: number
    entry?: number
    sl?: number
    tps?: number[]
    action?: string
    instructions?: string[]
    rr_pips?: number
  }
  consensus_reason?: string
  how_it_works?: string[]
}

type CommandCenterPayload = {
  symbol: string
  timeframe: string
  confluence: {
    technical_score: number
    direction: string
    ready: boolean
    buy_count: number
    sell_count: number
    indicators: IndicatorRow[]
    entry?: number
    sl?: number
    tps?: number[]
  }
  latest_decision: Decision | null
  decisions: Decision[]
  worldmonitor?: {
    source?: string
    news_score?: number
    macro_bias?: string
    geopolitical_risk?: string
    headlines?: string[]
    price_change_pct?: number
  }
  live_price?: { price?: number; yf_symbol?: string; asof?: string; source?: string }
  paper?: { agent_id?: number; cash?: number; starting_cash?: number; paper_only?: string }
  webhook_setup?: string
}

function biasClass(side?: string) {
  const s = (side || '').toUpperCase()
  if (['BUY', 'LONG', 'BULLISH', 'APPROVED'].includes(s)) return 'uti-pos'
  if (['SELL', 'SHORT', 'BEARISH', 'REJECTED'].includes(s)) return 'uti-neg'
  if (['WAIT', 'NEUTRAL', 'MISSING'].includes(s)) return 'uti-neu'
  return 'uti-neu'
}

function Bar({ value, tone }: { value: number; tone: 'bull' | 'bear' }) {
  const width = Math.max(0, Math.min(100, value))
  return (
    <div className="uti-bar-track">
      <div className={`uti-bar-fill ${tone}`} style={{ width: `${width}%` }} />
    </div>
  )
}

export function CommandCenterPage() {
  const { language } = useLanguage()
  const zh = language === 'zh'
  const [symbol, setSymbol] = useState('XAUUSD')
  const [timeframe, setTimeframe] = useState('30')
  const [data, setData] = useState<CommandCenterPayload | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [provider, setProvider] = useState('heuristic')
  const [deepModel, setDeepModel] = useState('gpt-5.5')
  const [quickModel, setQuickModel] = useState('gpt-5.4-mini')
  const [debateRounds, setDebateRounds] = useState(2)
  const [paperEnabled, setPaperEnabled] = useState(true)
  const [killSwitch, setKillSwitch] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [toast, setToast] = useState<string | null>(null)

  const load = useCallback(async () => {
    setError(null)
    try {
      const res = await fetch(
        `${API_BASE}/uti/command-center?symbol=${encodeURIComponent(symbol)}&timeframe=${encodeURIComponent(timeframe)}`
      )
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const payload = await res.json()
      setData(payload)
      const settingsRes = await fetch(`${API_BASE}/uti/settings`)
      if (settingsRes.ok) {
        const s = await settingsRes.json()
        const env = s.env || {}
        setProvider(env.llm_provider || 'heuristic')
        setDeepModel(env.deep_model || 'gpt-5.5')
        setQuickModel(env.quick_model || 'gpt-5.4-mini')
        setDebateRounds(Number(env.max_debate_rounds || 2))
        setPaperEnabled(String(env.paper_trade_enabled) !== 'false')
        setKillSwitch(String(env.kill_switch) === 'true')
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    }
  }, [symbol, timeframe])

  useEffect(() => {
    load()
    const id = window.setInterval(load, 15000)
    return () => window.clearInterval(id)
  }, [load])

  const latest = data?.latest_decision
  const indicators = data?.confluence?.indicators || []

  const title = useMemo(
    () => (zh ? 'AI 交易指挥中心' : 'AI Trading Command Center'),
    [zh]
  )

  const runDecision = async () => {
    setBusy(true)
    setToast(null)
    try {
      const res = await fetch(`${API_BASE}/uti/decisions/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol, timeframe, force: true }),
      })
      const body = await res.json()
      if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`)
      setToast(zh ? '决策已生成' : 'Decision generated')
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setBusy(false)
    }
  }

  const saveSettings = async (e: FormEvent) => {
    e.preventDefault()
    setBusy(true)
    try {
      const res = await fetch(`${API_BASE}/uti/settings`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          llm_provider: provider,
          deep_model: deepModel,
          quick_model: quickModel,
          max_debate_rounds: debateRounds,
          paper_trade_enabled: paperEnabled,
          kill_switch: killSwitch,
        }),
      })
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      setToast(zh ? '设置已保存' : 'Settings saved')
      setSettingsOpen(false)
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="uti-page">
      <div className="uti-header">
        <div>
          <div className="uti-kicker">Unified Trading Intelligence · Paper</div>
          <h1>{title}</h1>
        </div>
        <div className="uti-controls">
          <input value={symbol} onChange={(e) => setSymbol(e.target.value.toUpperCase())} aria-label="symbol" />
          <select value={timeframe} onChange={(e) => setTimeframe(e.target.value)} aria-label="timeframe">
            {['1', '5', '15', '30', '60', '240', 'D'].map((tf) => (
              <option key={tf} value={tf}>{tf}</option>
            ))}
          </select>
          <button type="button" onClick={load} disabled={busy}>{zh ? '刷新' : 'Refresh'}</button>
          <button type="button" className="uti-primary" onClick={runDecision} disabled={busy}>
            {zh ? '强制决策' : 'Force Decide'}
          </button>
          <button type="button" onClick={() => setSettingsOpen((v) => !v)}>
            {zh ? 'AI 设置' : 'AI Settings'}
          </button>
        </div>
      </div>

      {error && <div className="uti-banner error">{error}</div>}
      {toast && <div className="uti-banner ok">{toast}</div>}

      <div className="uti-grid" style={{ marginBottom: 16 }}>
        <div className="uti-card">
          <div className="uti-kicker">{zh ? '实盘报价' : 'Live price'}</div>
          <div style={{ fontSize: 28, fontWeight: 700 }}>
            {data?.live_price?.price != null ? `$${Number(data.live_price.price).toLocaleString()}` : '—'}
          </div>
          <div className="uti-muted">
            {data?.live_price?.yf_symbol || symbol} · {data?.live_price?.source || 'pending'}
          </div>
        </div>
        <div className="uti-card">
          <div className="uti-kicker">{zh ? '纸面资金' : 'Paper balance'}</div>
          <div style={{ fontSize: 28, fontWeight: 700 }}>
            ${Number(data?.paper?.cash ?? 100).toFixed(2)}
          </div>
          <div className="uti-muted">
            {zh ? '起始' : 'Start'} ${Number(data?.paper?.starting_cash ?? 100).toFixed(0)} · agent #{data?.paper?.agent_id ?? '—'}
          </div>
        </div>
        <div className="uti-card">
          <div className="uti-kicker">WorldMonitor</div>
          <div className={biasClass(data?.worldmonitor?.macro_bias)} style={{ fontSize: 22, fontWeight: 700 }}>
            {data?.worldmonitor?.macro_bias || '—'}
          </div>
          <div className="uti-muted">
            news {data?.worldmonitor?.news_score ?? '—'} · geo {data?.worldmonitor?.geopolitical_risk || '—'}
          </div>
          <div className="uti-muted" style={{ marginTop: 6 }}>
            {(data?.worldmonitor?.headlines || []).slice(0, 2).join(' · ') || (zh ? '暂无头条' : 'No headlines yet')}
          </div>
        </div>
        <div className="uti-card">
          <div className="uti-kicker">TradingView Pro</div>
          <div style={{ fontSize: 14, lineHeight: 1.45 }}>
            {zh
              ? '用 ngrok 暴露 :8000，然后打开 webhook 配置拿 5 条告警 URL。'
              : 'Tunnel :8000 with ngrok, then open webhook setup for all 5 alert URLs.'}
          </div>
          <a href={`${API_BASE}/uti/webhooks/setup`} target="_blank" rel="noreferrer" style={{ color: 'var(--accent-primary)' }}>
            {data?.webhook_setup || '/api/uti/webhooks/setup'}
          </a>
        </div>
      </div>

      {settingsOpen && (
        <form className="uti-settings" onSubmit={saveSettings}>
          <h3>{zh ? '模型与风控' : 'Models & Risk'}</h3>
          <label>
            {zh ? 'AI 提供商' : 'AI Provider'}
            <select value={provider} onChange={(e) => setProvider(e.target.value)}>
              {['heuristic', 'openai', 'anthropic', 'google', 'xai', 'deepseek', 'openrouter', 'ollama'].map((p) => (
                <option key={p} value={p}>{p}</option>
              ))}
            </select>
          </label>
          <label>
            {zh ? '深度模型' : 'Deep model'}
            <input value={deepModel} onChange={(e) => setDeepModel(e.target.value)} />
          </label>
          <label>
            {zh ? '快速模型' : 'Quick model'}
            <input value={quickModel} onChange={(e) => setQuickModel(e.target.value)} />
          </label>
          <label>
            {zh ? '多空辩论轮数' : 'Bull/Bear debate rounds'}
            <input
              type="number"
              min={1}
              max={5}
              value={debateRounds}
              onChange={(e) => setDebateRounds(Number(e.target.value))}
            />
          </label>
          <label className="uti-check">
            <input type="checkbox" checked={paperEnabled} onChange={(e) => setPaperEnabled(e.target.checked)} />
            {zh ? '启用纸面成交' : 'Paper trade enabled'}
          </label>
          <label className="uti-check">
            <input type="checkbox" checked={killSwitch} onChange={(e) => setKillSwitch(e.target.checked)} />
            {zh ? '紧急停止' : 'Kill switch'}
          </label>
          <button type="submit" className="uti-primary" disabled={busy}>{zh ? '保存' : 'Save'}</button>
        </form>
      )}

      <div className="uti-grid">
        <section className="uti-panel uti-hero">
          <div className="uti-symbol-row">
            <div>
              <div className="uti-symbol">{data?.symbol || symbol}</div>
              <div className="uti-tf">{data?.timeframe || timeframe}M</div>
            </div>
            <div className={`uti-pill ${biasClass(latest?.show_signal ? latest?.decision : 'WAIT')}`}>
              {latest?.show_signal ? (latest?.signal_label || latest?.decision) : (zh ? '暂无信号' : 'NO SIGNAL')}
            </div>
          </div>
          {!latest?.show_signal && (
            <div className="uti-muted" style={{ marginBottom: 10 }}>
              {zh
                ? '只在高质量对齐时显示买卖。研究人员在后台运行；冲突或弱信号保持静默。'
                : 'Signals appear only on high-quality aligned setups. Researchers run quietly until then.'}
              {latest?.signal_quality?.quality_score != null && (
                <> · quality {latest.signal_quality.quality_score}/100</>
              )}
            </div>
          )}
          <div className="uti-metrics">
            <div><span>{zh ? '技术分' : 'Technical'}</span><strong>{data?.confluence?.technical_score ?? '—'}/100</strong></div>
            <div><span>{zh ? 'AI 置信' : 'AI Confidence'}</span><strong>{latest?.ai_confidence ?? '—'}/100</strong></div>
            <div><span>{zh ? '新闻' : 'News'}</span><strong>{latest?.news_score ?? '—'}</strong></div>
            <div><span>{zh ? '宏观' : 'Macro'}</span><strong className={biasClass(latest?.macro_bias)}>{latest?.macro_bias || '—'}</strong></div>
            <div><span>{zh ? '风险' : 'Risk'}</span><strong className={biasClass(latest?.risk?.status)}>{latest?.risk?.status || '—'}</strong></div>
          </div>
          {latest?.show_signal && latest?.pip_plan?.message && (
            <div className="uti-card" style={{ marginTop: 12, borderColor: '#3d6b4f' }}>
              <div className="uti-kicker">{zh ? '点差计划 (自动)' : 'Pip plan (auto)'}</div>
              <div style={{ fontWeight: 600, marginBottom: 8 }}>{latest.pip_plan.message}</div>
              <div className="uti-muted">
                Stop {latest.pip_plan.stop_pips} pips · TP1 {latest.pip_plan.tp1_pips} · TP2 {latest.pip_plan.tp2_pips}
                {latest.pip_plan.rr_pips != null ? ` · R:R ${latest.pip_plan.rr_pips}` : ''}
              </div>
              <ul style={{ margin: '8px 0 0', paddingLeft: 18 }}>
                {(latest.pip_plan.instructions || []).map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            </div>
          )}
        </section>

        <section className="uti-panel">
          <h2>{zh ? '五个指标' : 'Five Indicators'}</h2>
          <div className="uti-table">
            {indicators.map((row) => (
              <div key={row.indicator_id} className="uti-row">
                <span>{row.name}</span>
                <span className={biasClass(row.side)}>{row.side === 'MISSING' ? (zh ? '自检中' : 'you check') : row.side}</span>
                <span>{row.side === 'MISSING' ? '—' : Math.round((row.strength || 0) * 100)}</span>
              </div>
            ))}
          </div>
          <div className="uti-scoreline">
            {zh ? '技术综合分' : 'Technical Score'}{' '}
            <strong>{data?.confluence?.technical_score ?? '—'}/100</strong>
            {!data?.confluence?.ready && (
              <em>{zh ? '（你自行看盘；AI 研究仍运行）' : '(you read charts; AI research still runs)'}</em>
            )}
          </div>
        </section>

        <section className="uti-panel">
          <h2>{zh ? 'AI 研究 (后台)' : 'AI Research (background)'}</h2>
          <div className="uti-muted" style={{ marginBottom: 8 }}>
            {zh
              ? '这些是研究投票，不是最终信号。只有对齐的高质量交易才显示 BUY/SELL + 点差。'
              : 'These are research votes, not the trade signal. BUY/SELL + pips only when quality passes.'}
          </div>
          <div className="uti-analysts">
            {Object.entries(latest?.analysts || {
              technical: { bias: data?.confluence?.direction, score: data?.confluence?.technical_score },
            }).map(([key, val]) => (
              <div key={key} className="uti-row">
                <span>{key}</span>
                <span className={biasClass(val?.bias)}>{val?.bias || '—'}</span>
                <span>{val?.score ?? '—'}</span>
              </div>
            ))}
          </div>
          <div className="uti-debate">
            <div>
              <div className="uti-row"><span>Bull Research</span><strong>{latest?.bull_research ?? '—'}</strong></div>
              <Bar value={Number(latest?.bull_research || 0)} tone="bull" />
            </div>
            <div>
              <div className="uti-row"><span>Bear Research</span><strong>{latest?.bear_research ?? '—'}</strong></div>
              <Bar value={Number(latest?.bear_research || 0)} tone="bear" />
            </div>
          </div>
          <div className="uti-metrics compact">
            <div><span>{zh ? '信号' : 'Signal'}</span><strong className={biasClass(latest?.show_signal ? latest?.trader : 'WAIT')}>{latest?.show_signal ? (latest?.trader || '—') : 'NO SIGNAL'}</strong></div>
            <div><span>Risk</span><strong className={biasClass(latest?.risk?.status)}>{latest?.risk?.status || '—'}</strong></div>
          </div>
          {latest?.consensus_reason && (
            <div className="uti-muted" style={{ marginTop: 8 }}>{latest.consensus_reason}</div>
          )}
        </section>
      </div>

      <section className="uti-panel uti-history">
        <h2>{zh ? '决策审计' : 'Decision Audit'}</h2>
        <div className="uti-history-list">
          {(data?.decisions || []).map((d) => (
            <div key={d.trade_number} className="uti-history-item">
              <div className="uti-history-top">
                <strong>{d.trade_label}</strong>
                <span className={biasClass(d.decision)}>{d.decision}</span>
                <span>{d.created_at}</span>
              </div>
              <div className="uti-history-meta">
                {d.show_signal ? d.decision : 'NO SIGNAL'} · Tech {d.technical_score} · AI {d.ai_confidence} · Paper {d.paper_status}
                {d.pip_plan?.stop_pips != null && d.show_signal ? ` · SL ${d.pip_plan.stop_pips}p TP ${d.pip_plan.tp1_pips}p` : ''}
              </div>
            </div>
          ))}
          {!data?.decisions?.length && (
            <div className="uti-empty">{zh ? '暂无决策。发送 Pine webhook 或点击强制决策。' : 'No decisions yet. Send Pine webhooks or force a decision.'}</div>
          )}
        </div>
      </section>

      <style>{`
        .uti-page { padding: 20px 24px 48px; color: var(--text-primary); }
        .uti-header { display:flex; justify-content:space-between; gap:16px; align-items:flex-start; margin-bottom:18px; flex-wrap:wrap; }
        .uti-kicker { color: var(--text-muted); font-size:12px; letter-spacing:0.08em; text-transform:uppercase; }
        .uti-header h1 { margin:6px 0 0; font-size:28px; }
        .uti-controls { display:flex; gap:8px; flex-wrap:wrap; }
        .uti-controls input, .uti-controls select, .uti-settings input, .uti-settings select {
          background: #12171f; border:1px solid #2a3342; color:inherit; border-radius:8px; padding:8px 10px;
        }
        .uti-controls button, .uti-settings button {
          background:#1b2330; border:1px solid #314056; color:inherit; border-radius:8px; padding:8px 12px; cursor:pointer;
        }
        .uti-primary { background:#1f6feb !important; border-color:#1f6feb !important; }
        .uti-banner { margin-bottom:12px; padding:10px 12px; border-radius:8px; }
        .uti-banner.error { background:#3b1418; color:#ffb4b4; }
        .uti-banner.ok { background:#12301f; color:#b6f3c8; }
        .uti-settings { display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:12px; margin-bottom:16px; padding:16px; border:1px solid #243041; border-radius:12px; background:#0f141c; }
        .uti-settings label { display:flex; flex-direction:column; gap:6px; font-size:13px; color:var(--text-muted); }
        .uti-check { flex-direction:row !important; align-items:center; }
        .uti-grid { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:14px; }
        @media (max-width: 1100px) { .uti-grid { grid-template-columns:1fr; } }
        .uti-panel { background:linear-gradient(180deg,#121821,#0e131a); border:1px solid #243041; border-radius:14px; padding:16px; }
        .uti-panel h2 { margin:0 0 12px; font-size:15px; color:#9fb0c7; text-transform:uppercase; letter-spacing:0.06em; }
        .uti-symbol { font-size:28px; font-weight:700; }
        .uti-tf { color:#8ea0b8; }
        .uti-symbol-row { display:flex; justify-content:space-between; align-items:center; margin-bottom:14px; }
        .uti-pill { padding:8px 14px; border-radius:999px; font-weight:700; background:#1a2230; }
        .uti-metrics { display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }
        .uti-metrics.compact { margin-top:12px; }
        .uti-metrics div { background:#0c1017; border-radius:10px; padding:10px; display:flex; flex-direction:column; gap:4px; }
        .uti-metrics span { color:#8091a8; font-size:12px; }
        .uti-table, .uti-analysts { display:flex; flex-direction:column; gap:8px; }
        .uti-row { display:grid; grid-template-columns:1fr auto auto; gap:10px; align-items:center; padding:8px 0; border-bottom:1px solid #1c2533; }
        .uti-scoreline { margin-top:12px; color:#c5d2e3; }
        .uti-scoreline em { color:#8091a8; margin-left:8px; font-style:normal; }
        .uti-debate { display:flex; flex-direction:column; gap:10px; margin:12px 0; }
        .uti-bar-track { height:8px; background:#1a2230; border-radius:999px; overflow:hidden; }
        .uti-bar-fill.bull { height:100%; background:#2f9e6b; }
        .uti-bar-fill.bear { height:100%; background:#c44d58; }
        .uti-levels { display:grid; grid-template-columns:1fr 1fr; gap:8px; color:#c5d2e3; font-size:13px; }
        .uti-pos { color:#3dd68c; }
        .uti-neg { color:#ff6b7a; }
        .uti-neu { color:#d0b45a; }
        .uti-history { margin-top:14px; }
        .uti-history-list { display:flex; flex-direction:column; gap:10px; }
        .uti-history-item { padding:12px; border-radius:10px; background:#0c1017; border:1px solid #1c2533; }
        .uti-history-top { display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
        .uti-history-meta { margin-top:6px; color:#8091a8; font-size:12px; }
        .uti-empty { color:#8091a8; padding:12px 0; }
      `}</style>
    </div>
  )
}
