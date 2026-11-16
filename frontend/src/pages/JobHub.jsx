import { useCallback, useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import LoginRequired from '../components/LoginRequired'
import JobListPanel from '../components/jobs/JobListPanel'
import JobStatsPanel from '../components/jobs/JobStatsPanel'
import JobStatusPicker from '../components/jobs/JobStatusPicker'
import JobImportDialog from '../components/jobs/JobImportDialog'
import {
  clearJobs,
  deleteJob,
  getJobStats,
  listJobs,
  notify,
  seedJobs,
  setJobProgress,
} from '../api/client'
import { isApplied } from '../constants/jobStatus'
import { useAuth } from '../auth/AuthContext'
import { IconBriefcase, IconChart, IconList, IconRefresh } from '../components/icons'
import './job-hub.css'

/**
 * 岗位信息汇总（共享岗位库 + 各人独立投递进度 + 投递统计）
 *
 * 在整体动线里的位置：**首页 → 岗位信息汇总 → 模拟面试**。
 * 「先看有哪些岗、投到哪一步了，再针对目标岗位练面试」——这是把它排在首页之后的产品理由：
 * 面试练习的目标岗位本来就来自这份汇总表。
 *
 * 三处**有意为之的取舍**（不是遗漏）：
 *
 * 1. **不引入第二套账号体系**。岗位库的维护（导入 / 载入示例 / 清空 / 删除）
 *    与投递进度共用本项目的登录态：**登录即可维护**，进度只属于自己。
 *    页面不再区分「管理员」，后端也不再提供那套管理员专属入口——
 *    隐藏按钮本来就不是权限，权限只由后端那句 `require_login` 表达。
 * 2. **整页要求登录**。本项目的游客走进程内内存库，岗位库在那个库里是空的——
 *    放行只会得到一个「岗位库是空的」假象，与「游客模式下不展示任何数据」
 *    的口径也自相矛盾。因此游客看到的是说明卡。
 * 3. **布局是「标签 + 卡片」**。它接在本项目已有的顶部导航 / 底部 tab 体系里，
 *    样式全部走语义令牌：不引入新的设计语言，深浅色、焦点环、触控目标
 *    全部沿用全局规范。
 *
 * 标签状态放在 URL（`?tab=`）里而不是组件 state：刷新、收藏、从别处直接跳到
 * 「投递统计」都能用同一个链接表达（与「我的面试」的做法一致）。
 */

/* 两个标签各对应一种视图：维护动作（导入 / 清空 / 删除）在「汇总列表」里就地提供，
   不单独占一个标签，避免同一件事出现两个入口。 */
const TABS = [
  { key: 'list', label: '汇总列表', Icon: IconList },
  { key: 'stats', label: '投递统计', Icon: IconChart },
]

export default function JobHub() {
  const { isGuest, loading: authLoading } = useAuth()
  const [params, setParams] = useSearchParams()

  const [records, setRecords] = useState([])
  const [stats, setStats] = useState(null)
  const [note, setNote] = useState('')
  // 昨天新入库的岗位条数：由服务端按本地时区统计（不受筛选/搜索影响）。
  // 单独存一份而不是从 records 现算，是因为「昨天」的判定必须以服务端时区为准，
  // 前端拿 posted_at 字符串自己比日期会在跨时区访问时对不上。
  const [postedYesterday, setPostedYesterday] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const [picking, setPicking] = useState(null)
  const [savingProgress, setSavingProgress] = useState(false)
  const [importOpen, setImportOpen] = useState(false)

  const tabParam = params.get('tab')
  const tab = TABS.some((t) => t.key === tabParam) ? tabParam : 'list'

  const setTab = (key) => {
    const next = new URLSearchParams(params)
    next.set('tab', key)
    setParams(next, { replace: true })
  }

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      /* 列表与统计一起拉：统计的聚合口径在服务端，列表的记录在本地做筛选，
         两者都基于同一份进度数据，分成两次请求才不会出现「列表改了、统计没跟上」。 */
      const listRes = await listJobs()
      setRecords(Array.isArray(listRes?.items) ? listRes.items : [])
      setNote(listRes?.note || '')
      setPostedYesterday(Number(listRes?.posted_yesterday) || 0)
      setStats(await getJobStats().catch(() => null))
    } catch (err) {
      setError(err?.message || '岗位库加载失败')
      setRecords([])
      setStats(null)
      setPostedYesterday(0)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    if (authLoading || isGuest) return
    load()
  }, [authLoading, isGuest, load])

  /** 只刷新统计（改完进度后调用）：列表已经在本地更新过，不必整页重拉 */
  const reloadStats = useCallback(async () => {
    try {
      setStats(await getJobStats())
    } catch {
      /* 统计拉不到不影响列表操作，静默降级（下一次进页面会重拉） */
    }
  }, [])

  const onPickStatus = async (status) => {
    const record = picking
    if (!record) return
    setSavingProgress(true)
    try {
      const res = await setJobProgress(record.job_id, status)
      // 本地就地更新，避免整表重拉导致滚动位置丢失（长列表上很烦）
      setRecords((prev) =>
        prev.map((r) =>
          r.job_id === record.job_id
            ? { ...r, progress: res?.status || status, progress_at: res?.progress_at || '' }
            : r
        )
      )
      notify(res?.message || `投递进度已更新为「${status}」`, 'success')
      setPicking(null)
      await reloadStats()
    } catch {
      /* client.js 已经弹过 Toast，这里保持弹层打开让用户重试 */
    } finally {
      setSavingProgress(false)
    }
  }

  /* ---------------- 岗位库维护动作（登录即可操作） ---------------- */

  const onSeed = async () => {
    setBusy(true)
    try {
      const res = await seedJobs()
      notify(res?.message || '示例数据已载入', 'success', 3600)
      await load()
    } catch {
      /* client.js 已提示 */
    } finally {
      setBusy(false)
    }
  }

  const onClearAll = async () => {
    const mine = records.filter((r) => isApplied(r.progress)).length
    const ok = window.confirm(
      `确定要清空全部岗位吗？\n\n共 ${records.length} 条岗位会被删除，并【连带清除所有人】在这些岗位上的投递进度（不可撤销）。` +
        (mine ? `\n其中包含你自己的 ${mine} 条已投递记录。` : '')
    )
    if (!ok) return
    setBusy(true)
    try {
      const res = await clearJobs()
      notify(res?.message || '岗位库已清空', 'success', 3600)
      await load()
    } catch {
      /* client.js 已提示 */
    } finally {
      setBusy(false)
    }
  }

  const onDelete = async (record) => {
    const ok = window.confirm(
      `确定删除「${record.company}」吗？\n\n这会连带清除所有人在该岗位上的投递进度（不可撤销）。`
    )
    if (!ok) return
    setBusy(true)
    try {
      const res = await deleteJob(record.job_id)
      notify(res?.message || '岗位已删除', 'success')
      await load()
    } catch {
      /* client.js 已提示 */
    } finally {
      setBusy(false)
    }
  }

  /* ---------------- 渲染 ---------------- */

  if (!authLoading && isGuest) {
    return (
      <LoginRequired
        what="岗位信息汇总"
        points={[
          '全员共享的岗位库：企业、雇主类型、行业、毕业届别、地点、专业、岗位与投递链接',
          '只属于你自己的投递进度（15 个状态，从简历初筛到 Offer）',
          '投递统计：漏斗、各环节淘汰、进度与雇主类型分布，可导出统计 Excel',
          '按雇主类型 / 行业 / 毕业届别 / 专业 / 地点 / 岗位筛选与搜索，每页 30 条翻页查看',
        ]}
      />
    )
  }

  const appliedCount = records.filter((r) => isApplied(r.progress)).length

  return (
    <div className="container">
      <div className="page-head">
        <div>
          <h1 className="page-title">
            <IconBriefcase size={24} className="jh-title-ic" /> 岗位信息汇总
          </h1>
          <p className="page-sub">
            {records.length
              ? `共 ${records.length} 条岗位 · 你已投递 ${appliedCount} 条 · 岗位信息全员共享，投递进度只属于你自己`
              : '岗位信息全员共享，投递进度各自独立；先找到目标岗位，再针对它练面试'}
          </p>
        </div>
        <div className="row gap-8" style={{ flexWrap: 'wrap' }}>
          <button className="btn btn--ghost btn--sm" onClick={load} disabled={loading || busy}>
            <IconRefresh size={15} /> 刷新
          </button>
        </div>
      </div>

      {/* 子标签：tablist/tab/tabpanel 语义 + 左右方向键切换（键盘可达） */}
      <div className="jh-tabs" role="tablist" aria-label="岗位信息汇总内容">
        {TABS.map((t) => (
          <button
            key={t.key}
            role="tab"
            id={`jh-tab-${t.key}`}
            aria-selected={tab === t.key}
            aria-controls={`jh-panel-${t.key}`}
            tabIndex={tab === t.key ? 0 : -1}
            className={`jh-tab ${tab === t.key ? 'on' : ''}`}
            onClick={() => setTab(t.key)}
            onKeyDown={(e) => {
              if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return
              e.preventDefault()
              const i = TABS.findIndex((x) => x.key === tab)
              const next = e.key === 'ArrowRight' ? (i + 1) % TABS.length : (i - 1 + TABS.length) % TABS.length
              setTab(TABS[next].key)
              document.getElementById(`jh-tab-${TABS[next].key}`)?.focus()
            }}
          >
            <t.Icon size={16} /> {t.label}
          </button>
        ))}
      </div>

      <div className="jh-panel-wrap" role="tabpanel" id={`jh-panel-${tab}`} aria-labelledby={`jh-tab-${tab}`} tabIndex={-1}>
        {loading ? (
          <div className="card">
            <div className="skeleton sk-line sk-line--40" />
            <div className="skeleton sk-line" style={{ marginTop: 10 }} />
            <div className="skeleton sk-line sk-line--60" style={{ marginTop: 10 }} />
            <div className="skeleton sk-line" style={{ marginTop: 10 }} />
          </div>
        ) : error ? (
          <div className="card">
            <div className="empty">
              <div className="empty-title">岗位库加载失败</div>
              <div className="empty-desc">{error}</div>
              <button className="btn btn--primary mt-16" onClick={load}>
                重试
              </button>
            </div>
          </div>
        ) : (
          <>
            {tab === 'list' && (
              <JobListPanel
                records={records}
                postedYesterday={postedYesterday}
                busy={busy}
                onPickStatus={setPicking}
                onImport={() => setImportOpen(true)}
                onSeed={onSeed}
                onClearAll={onClearAll}
                onDelete={onDelete}
              />
            )}
            {tab === 'stats' && (
              <JobStatsPanel
                records={records}
                stats={stats}
                busy={busy}
                onPickStatus={setPicking}
                onGoList={() => setTab('list')}
              />
            )}
          </>
        )}
      </div>

      {note && !loading && !error && (
        <p className="jh-foot-note">{note}</p>
      )}

      {picking && (
        <JobStatusPicker
          record={picking}
          saving={savingProgress}
          onPick={onPickStatus}
          onClose={() => setPicking(null)}
        />
      )}

      {importOpen && (
        <JobImportDialog
          onClose={() => setImportOpen(false)}
          onDone={async () => {
            await load()
          }}
        />
      )}
    </div>
  )
}
