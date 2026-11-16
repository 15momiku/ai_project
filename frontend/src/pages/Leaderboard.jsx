import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  IconCheck,
  IconInfo,
  IconPlay,
  IconTarget,
  IconTrophy,
  IconTrendUp,
  IconWarn,
} from '../components/icons'
import { getGrowthSummary, getLeaderboard } from '../api/client'
import './leaderboard.css'

const TABS = [
  { key: 'job', label: '同岗位榜' },
  { key: 'school', label: '同校榜' },
  { key: 'progress', label: '进步榜' },
  { key: 'growth', label: '我的成长' },
]

/* 前三名奖牌：用矢量奖杯 + 名次色，避免 emoji 在不同系统渲染不一 */
const MEDAL_COLORS = ['#b8860b', '#7a7f87', '#a0522d']

export default function Leaderboard() {
  const nav = useNavigate()
  const [type, setType] = useState('job')
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  /* ---- 体验增强 §12：我的成长面板 ---- */
  const [growth, setGrowth] = useState(null)
  const [growthLoading, setGrowthLoading] = useState(false)

  // 请求序号：快速切换 tab 时两个请求会并发，先发的慢响应会覆盖后发的结果，
  // 导致页面上显示的是上一个 tab 的榜单。只接受最新一次请求的响应。
  const seqRef = useRef(0)

  const load = useCallback(async (t) => {
    // 「我的成长」走自己的接口，不查榜单（两者数据源不同）
    if (t === 'growth') {
      const seq = (seqRef.current += 1)
      setGrowthLoading(true)
      setError('')
      try {
        const res = await getGrowthSummary()
        if (seq !== seqRef.current) return
        setGrowth(res)
      } catch (err) {
        if (seq !== seqRef.current) return
        setError(err?.message || '成长数据加载失败')
        setGrowth(null)
      } finally {
        if (seq === seqRef.current) setGrowthLoading(false)
      }
      return
    }
    const seq = (seqRef.current += 1)
    setLoading(true)
    setError('')
    try {
      const res = await getLeaderboard(t)
      if (seq !== seqRef.current) return
      setData(res)
    } catch (err) {
      if (seq !== seqRef.current) return
      setError(err?.message || '排行榜加载失败')
      setData(null)
    } finally {
      if (seq === seqRef.current) setLoading(false)
    }
  }, [])

  useEffect(() => {
    load(type)
  }, [type, load])

  const items = data?.items || []
  const isGrowth = type === 'growth'
  const busy = isGrowth ? growthLoading : loading

  return (
    <div className="container">
      <div className="page-head">
        <div>
          <h1 className="page-title">排行榜</h1>
          <p className="page-sub">看看同岗位、同校同学都在什么水平线，找到你的下一个目标</p>
        </div>
        <button className="btn btn--ghost btn--sm" onClick={() => load(type)}>
          刷新
        </button>
      </div>

      <div className="seg lb-seg">
        {TABS.map((t) => (
          <button key={t.key} className={type === t.key ? 'on' : ''} onClick={() => setType(t.key)}>
            {t.label}
          </button>
        ))}
      </div>

      {busy ? (
        <div className="card mt-16">
          {Array.from({ length: 6 }).map((_, i) => (
            <div className="skeleton sk-line" key={i} style={{ height: 36, marginBottom: 10 }} />
          ))}
        </div>
      ) : error ? (
        <div className="card mt-16">
          <div className="empty">
            <IconWarn size={26} style={{ color: 'var(--text-mute)' }} />
            <div className="empty-title">{isGrowth ? '成长数据加载失败' : '排行榜加载失败'}</div>
            <div className="empty-desc">{error}</div>
            <button className="btn btn--primary mt-16" onClick={() => load(type)}>
              重试
            </button>
          </div>
        </div>
      ) : isGrowth ? (
        /* ================= 我的成长面板（体验增强 §12）=================
           只陈述事实：连续几天、这周练了几场、各维度从多少到多少；
           断签不惩罚，目标是"练够 100 次"的长期主义。 */
        <div className="gr-wrap mt-16">
          {!growth || growth.total_sessions === 0 ? (
            <div className="card">
              <div className="empty">
                <IconTrendUp size={28} style={{ color: 'var(--text-mute)' }} />
                <div className="empty-title">还没有可统计的练习</div>
                <div className="empty-desc">
                  完成第一场模拟面试后，这里会显示连续打卡、每周练习量与各维度的变化。
                </div>
                <button className="btn btn--primary mt-16" onClick={() => nav('/setup')}>
                  <IconPlay size={16} /> 去做第一场模拟面试
                </button>
              </div>
            </div>
          ) : (
            <>
              <div className="card gr-hero">
                <div className="row row--between row--wrap gap-12">
                  <div>
                    <div className="gr-streak mono">
                      {growth.streak_days}
                      <span className="gr-streak-unit">天</span>
                    </div>
                    <div className="gr-streak-label">
                      连续练习
                      {growth.best_streak > growth.streak_days
                        ? ` · 最长纪录 ${growth.best_streak} 天`
                        : growth.streak_days > 0
                          ? ' · 正在刷新你的纪录'
                          : ''}
                    </div>
                  </div>
                  <div className="gr-stats">
                    <span className="chip mono">累计 {growth.total_sessions} 场</span>
                    <span className="chip mono">练习 {growth.total_minutes} 分钟</span>
                    <span className="chip mono">活跃 {growth.active_days} 天</span>
                  </div>
                </div>

                {/* 近 7 天格子：done 用主色实心，未练用弱化底色 */}
                <div className="gr-week mt-16" role="img" aria-label={`近 7 天打卡：${(growth.last_7_days || []).filter((d) => d.done).length} 天有练习`}>
                  {(growth.last_7_days || []).map((d) => (
                    <div className={`gr-day${d.done ? ' is-done' : ''}${d.is_today ? ' is-today' : ''}`} key={d.date}>
                      <span className="gr-day-dot">{d.done ? <IconCheck size={14} /> : ''}</span>
                      <span className="gr-day-date mono">{d.date}</span>
                      <span className="gr-day-wd">周{d.weekday}</span>
                    </div>
                  ))}
                </div>
              </div>

              <div className="gr-grid">
                <div className="card gr-card">
                  <div className="block-label">
                    <IconTrendUp size={15} /> 近 4 周练习
                  </div>
                  <ul className="gr-weeks">
                    {(growth.weeks || []).map((w) => (
                      <li key={w.label}>
                        <span className="mono gr-week-label">{w.label}</span>
                        <span className="gr-week-bar">
                          <i style={{ width: `${Math.min(100, w.sessions * 20)}%` }} />
                        </span>
                        <span className="mono gr-week-val">
                          {w.sessions} 场{w.avg_score > 0 ? ` · ${w.avg_score} 分` : ''}
                        </span>
                      </li>
                    ))}
                  </ul>
                </div>

                <div className="card gr-card">
                  <div className="block-label">
                    <IconTarget size={15} /> 六维变化（首场 → 最近一场）
                  </div>
                  <ul className="gr-dims">
                    {(growth.dimension_delta || []).map((d) => (
                      <li key={d.key}>
                        <span className="gr-dim-name">{d.name}</span>
                        <span className="mono gr-dim-val">
                          {d.first} → {d.last}
                        </span>
                        <span className={`chip ${d.delta > 0 ? 'chip--ok' : d.delta < 0 ? 'chip--danger' : ''}`}>
                          {d.delta > 0 ? '+' : ''}
                          {d.delta}
                        </span>
                      </li>
                    ))}
                    {!(growth.dimension_delta || []).length && <li className="mute-2">暂无可比的历史维度</li>}
                  </ul>
                </div>
              </div>

              <div className="card gr-next mt-16">
                <div className="block-label" style={{ margin: 0 }}>
                  <IconInfo size={15} /> 下一步
                </div>
                <p className="gr-next-text">{growth.next_goal}</p>
                <div className="row gap-8 row--wrap">
                  <button className="btn btn--primary btn--sm" onClick={() => nav('/setup')}>
                    <IconPlay size={15} /> 开一场面试
                  </button>
                  <button className="btn btn--ghost btn--sm" onClick={() => nav('/my-interviews?tab=wrongbook')}>
                    去错题本做 3 题特训
                  </button>
                </div>
                {growth.note && (
                  <div className="mute-2" style={{ fontSize: 12, marginTop: 10 }}>
                    {growth.note}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      ) : items.length === 0 ? (
        <div className="card mt-16">
          <div className="empty">
            <IconTrophy size={28} style={{ color: 'var(--text-mute)' }} />
            <div className="empty-title">榜单暂时没有数据</div>
            <div className="empty-desc">完成一次模拟面试后，你就会出现在榜单里。</div>
            <button className="btn btn--primary mt-16" onClick={() => nav('/setup')}>
              去做一次模拟面试
            </button>
          </div>
        </div>
      ) : (
        <>
          <div className="card mt-16 lb-card">
            <div className="lb-title row row--between">
              <span className="card-title">{data?.title || '排行榜'}</span>
              <span className="chip mono">共 {items.length} 人</span>
            </div>
            <ol className="lb-list">
              {items.map((it) => {
                const top = it.rank <= 3
                return (
                  <li className={`lb-item ${top ? `lb-item--top lb-item--t${it.rank}` : ''}`} key={`${it.rank}-${it.nickname}`}>
                    <span className="lb-rank mono">
                      {top ? <IconTrophy size={18} style={{ color: MEDAL_COLORS[it.rank - 1] }} /> : it.rank}
                    </span>
                    <span className="lb-avatar">{String(it.nickname || '同').slice(0, 1)}</span>
                    <span className="grow lb-main">
                      <span className="lb-name">
                        {it.nickname}
                        {it.badge && <span className="lb-badge">{it.badge}</span>}
                      </span>
                      <span className="lb-sub">
                        {[it.school, it.job_title].filter(Boolean).join(' · ')}
                      </span>
                    </span>
                    <span className="lb-score mono">{Number(it.score || 0).toFixed(1)}</span>
                  </li>
                )
              })}
            </ol>
          </div>

          <div className="card mt-16 lb-my">
            <div className="row row--between row--wrap">
              <div>
                <div className="lb-my-k">我的排名</div>
                <div className="lb-my-v">
                  {data?.my_rank ? (
                    <>
                      第 <span className="mono lb-my-rank">{data.my_rank}</span> 名
                    </>
                  ) : (
                    '暂无排名（完成一次面试即可上榜）'
                  )}
                </div>
              </div>
              <button className="btn btn--primary btn--sm" onClick={() => nav('/setup')}>
                提升我的排名
              </button>
            </div>
            {data?.note && (
              <div className="lb-note">
                <IconInfo size={15} className="inline-ic" />
                {data.note}
              </div>
            )}
          </div>
        </>
      )}
    </div>
  )
}
