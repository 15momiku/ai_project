import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate, useSearchParams } from 'react-router-dom'
import ScoreTrend from '../components/ScoreTrend'
import LoginRequired from '../components/LoginRequired'
import RecordingsPanel from '../components/RecordingsPanel'
import { WrongBookPanel } from './WrongBook'
import { IconBook, IconDoc, IconMic, IconRefresh, IconTrendUp } from '../components/icons'
import { getReportHistory } from '../api/client'
import { useAuth } from '../auth/AuthContext'
import './my-interviews.css'

/**
 * 我的面试（登录用户专属）
 *
 * 三个子标签：面试报告 / 错题本 / 录音回放。都是「本人数据」，因此：
 *  · 游客模式下整页替换为「仅登录用户可见」的说明卡（数据本来就不存在）；
 *  · 数据全部按当前登录账号过滤（后端只认令牌，前端也按 user_id 隔离本机录音）。
 *
 * 标签状态放在 URL（`?tab=`）里而不是组件 state：这样刷新、收藏、从别处
 * 直接跳「错题本」标签都能用同一套链接表达。
 */
const TABS = [
  { key: 'report', label: '面试报告', Icon: IconDoc },
  { key: 'wrongbook', label: '错题本', Icon: IconBook },
  { key: 'recordings', label: '录音回放', Icon: IconMic },
]

export default function MyInterviews() {
  const nav = useNavigate()
  const [params, setParams] = useSearchParams()
  const { user, isGuest, loading: authLoading } = useAuth()
  const tab = TABS.some((t) => t.key === params.get('tab')) ? params.get('tab') : 'report'

  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const data = await getReportHistory()
      const list = Array.isArray(data?.items) ? data.items : []
      // 后端按时间升序返回；展示与曲线都用「左旧右新」，列表用「最近在前」
      setItems(list)
    } catch (err) {
      setError(err?.message || '报告列表加载失败')
      setItems([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (isGuest) return
    load()
  }, [load, isGuest])

  // 曲线与汇总看最近 10 场（后端已经只返回最近 10 场）
  const recent = useMemo(() => items.slice(-10), [items])
  const newestFirst = useMemo(() => [...items].reverse(), [items])
  const summary = useMemo(() => {
    if (!recent.length) return null
    const scores = recent.map((r) => Number(r.score) || 0)
    const last = scores[scores.length - 1]
    const prev = scores.length > 1 ? scores[scores.length - 2] : null
    return {
      count: recent.length,
      last,
      best: Math.max(...scores),
      avg: scores.reduce((a, b) => a + b, 0) / scores.length,
      delta: prev === null ? null : last - prev,
    }
  }, [recent])

  if (!authLoading && isGuest) {
    return (
      <LoginRequired
        what="我的面试"
        points={[
          '面试报告：近 10 场评分、逐题依据与改进计划',
          '错题本：低于 6 分的题自动收集，可一键重练',
          '录音回放：语音作答的录音（只存在本机）',
        ]}
      />
    )
  }

  const setTab = (key) => {
    const next = new URLSearchParams(params)
    next.set('tab', key)
    setParams(next, { replace: true })
  }

  return (
    <div className="container">
      <div className="page-head">
        <div>
          <h1 className="page-title">我的面试</h1>
          <p className="page-sub">报告、错题与录音回放，都只属于你自己</p>
        </div>
        {tab === 'report' && (
          <button className="btn btn--ghost btn--sm" onClick={load} disabled={loading}>
            <IconRefresh size={16} /> 刷新
          </button>
        )}
      </div>

      {/* 子标签：用 tablist/ tab / tabpanel 语义，键盘左右键可切换 */}
      <div className="mi-tabs" role="tablist" aria-label="我的面试内容">
        {TABS.map((t) => (
          <button
            key={t.key}
            role="tab"
            id={`mi-tab-${t.key}`}
            aria-selected={tab === t.key}
            aria-controls={`mi-panel-${t.key}`}
            tabIndex={tab === t.key ? 0 : -1}
            className={`mi-tab ${tab === t.key ? 'on' : ''}`}
            onClick={() => setTab(t.key)}
            onKeyDown={(e) => {
              if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
              e.preventDefault()
              const i = TABS.findIndex((x) => x.key === tab)
              const next = e.key === 'ArrowRight' ? (i + 1) % TABS.length : (i - 1 + TABS.length) % TABS.length
              setTab(TABS[next].key)
              document.getElementById(`mi-tab-${TABS[next].key}`)?.focus()
            }}
          >
            <t.Icon size={16} /> {t.label}
          </button>
        ))}
      </div>

      <div
        className="mi-panel"
        role="tabpanel"
        id={`mi-panel-${tab}`}
        aria-labelledby={`mi-tab-${tab}`}
        tabIndex={-1}
      >
        {tab === 'wrongbook' && <WrongBookPanel embedded />}

        {tab === 'recordings' && <RecordingsPanel userId={user?.user_id || ''} />}

        {tab === 'report' && (
          <>
            {loading ? (
              <div className="card">
                <div className="skeleton sk-line sk-line--40" />
                <div className="skeleton sk-line" style={{ marginTop: 10 }} />
                <div className="skeleton sk-line sk-line--60" style={{ marginTop: 10 }} />
              </div>
            ) : error ? (
              <div className="card">
                <div className="empty">
                  <div className="empty-title">报告列表加载失败</div>
                  <div className="empty-desc">{error}</div>
                  <button className="btn btn--primary mt-16" onClick={load}>
                    重试
                  </button>
                </div>
              </div>
            ) : !recent.length ? (
              <div className="card">
                <div className="empty">
                  <IconDoc size={30} style={{ color: 'var(--text-mute)' }} />
                  <div className="empty-title">还没有已完成的面试</div>
                  <div className="empty-desc">
                    跑完一场模拟面试，这里会出现六维评分报告、评分曲线与历史对比。
                  </div>
                  <button className="btn btn--primary mt-16" onClick={() => nav('/setup')}>
                    <IconMic size={16} /> 开始一场模拟面试
                  </button>
                </div>
              </div>
            ) : (
              <>
                {/* 汇总卡：分数趋势一眼可见 */}
                {summary && (
                  <div className="mi-summary">
                    <div className="mi-stat">
                      <span className="mi-stat-label">已完成场次</span>
                      <span className="mi-stat-value mono">{summary.count}</span>
                    </div>
                    <div className="mi-stat">
                      <span className="mi-stat-label">最近得分</span>
                      <span className="mi-stat-value mono">{summary.last.toFixed(1)}</span>
                    </div>
                    <div className="mi-stat">
                      <span className="mi-stat-label">最高分</span>
                      <span className="mi-stat-value mono">{summary.best.toFixed(1)}</span>
                    </div>
                    <div className="mi-stat">
                      <span className="mi-stat-label">平均分</span>
                      <span className="mi-stat-value mono">{summary.avg.toFixed(1)}</span>
                    </div>
                    <div className="mi-stat">
                      <span className="mi-stat-label">较上一场</span>
                      <span
                        className={`mi-stat-value mono ${
                          summary.delta === null ? '' : summary.delta >= 0 ? 'up' : 'down'
                        }`}
                      >
                        {summary.delta === null
                          ? '—'
                          : `${summary.delta >= 0 ? '↑' : '↓'}${Math.abs(summary.delta).toFixed(1)}`}
                      </span>
                    </div>
                  </div>
                )}

                {/* 近 10 场评分可视化曲线：点某个点直接打开那场的报告 */}
                <section className="card mi-chart">
                  <div className="card-head">
                    <div className="card-title">
                      <IconTrendUp size={16} /> 近 {recent.length} 场评分曲线
                    </div>
                    <span className="mute-2" style={{ fontSize: 12 }}>
                      点曲线上的点可打开那一场的报告
                    </span>
                  </div>
                  <ScoreTrend
                    items={recent}
                    ariaLabel={`近 ${recent.length} 场面试得分曲线`}
                    onPick={(it) => it.session_id && nav(`/report/${it.session_id}`)}
                    emptyTitle="还没有足够的场次"
                    emptyDesc="完成一场面试后这里就会出现你的评分曲线。"
                  />
                </section>

                {/* 列表：最近在前 */}
                <ul className="mi-list">
                  {newestFirst.map((r, i) => (
                    <li className="mi-item" key={r.session_id}>
                      <span className="mi-date mono">{r.date || '—'}</span>
                      <span className="mi-job">{r.job_title || '目标岗位'}</span>
                      {i === 0 && <span className="chip chip--accent">最近</span>}
                      <span className="mi-score mono">{Number(r.score) > 0 ? `${Number(r.score).toFixed(1)} 分` : '—'}</span>
                      <button
                        className="btn btn--ghost btn--sm"
                        onClick={() => nav(`/report/${r.session_id}`)}
                        aria-label={`查看 ${r.date || ''} ${r.job_title || '目标岗位'} 的报告`}
                      >
                        <IconDoc size={15} /> 查看报告
                      </button>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </>
        )}
      </div>
    </div>
  )
}
