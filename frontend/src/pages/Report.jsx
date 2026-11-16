import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import SectionCard from '../components/SectionCard'
import ScoreRing from '../components/ScoreRing'
import RadarChart from '../components/RadarChart'
import ScoreTrend from '../components/ScoreTrend'
import ReportProgress from '../components/ReportProgress'
import {
  IconBulb,
  IconCheck,
  IconCheckCircle,
  IconClock,
  IconDoc,
  IconDownload,
  IconInfo,
  IconMinus,
  IconRefresh,
  IconShare,
  IconSparkle,
  IconTarget,
  IconTrendUp,
  IconUser,
  IconWarn,
} from '../components/icons'
import { dimClass } from '../constants/dimensions'
import {
  exportReport,
  generateReport,
  getReport,
  getShare,
  loadLocal,
  notify,
  reportFeedback,
  reportQuestionFeedback,
  saveLocal,
} from '../api/client'
import './report.css'

/** 逐题反馈的三个选项（体验增强 §6：让用户能质疑评分，反馈用于校准 Prompt） */
const VERDICT_OPTIONS = [
  { key: 'accurate', label: '准确', icon: IconCheck },
  { key: 'high', label: '偏高', icon: IconTrendUp },
  { key: 'low', label: '偏低', icon: IconMinus },
]

/** 基准刻度：把本场分数放到 P25/P50/P75 上，让「7.2 分」有参照物 */
const BENCH_MARKS = [
  { key: 'p25', label: 'P25' },
  { key: 'p50', label: 'P50' },
  { key: 'p75', label: 'P75' },
]

/* 等级配色：S/A 绿、B 蓝、C 黄、D/E 红 */
const gradeClass = (grade = '') => {
  const g = String(grade).trim().charAt(0).toUpperCase()
  if (g === 'S' || g === 'A') return 'grade--ok'
  if (g === 'B') return 'grade--info'
  if (g === 'C') return 'grade--warn'
  return 'grade--danger'
}

const priorityMeta = {
  high: { label: '高优先级', cls: 'imp--high' },
  medium: { label: '中优先级', cls: 'imp--medium' },
  low: { label: '低优先级', cls: 'imp--low' },
}

const fmt = (n, d = 1) => (Number.isFinite(Number(n)) ? Number(n).toFixed(d) : '—')

/** 海报绘制（750 x 1334，配色与整体一致） */
function drawPoster(canvas, data) {
  const ctx = canvas.getContext('2d')
  const W = 750
  const H = 1334
  canvas.width = W
  canvas.height = H

  // 背景：白色卡片式海报（与界面浅色主题一致）
  const bg = ctx.createLinearGradient(0, 0, 0, H)
  bg.addColorStop(0, '#F7F8FA')
  bg.addColorStop(0.5, '#FFFFFF')
  bg.addColorStop(1, '#F7F8FA')
  ctx.fillStyle = bg
  ctx.fillRect(0, 0, W, H)

  const glow = (cx, cy, r, color, a) => {
    const g = ctx.createRadialGradient(cx, cy, 0, cx, cy, r)
    g.addColorStop(0, color)
    g.addColorStop(1, 'rgba(255,255,255,0)')
    ctx.globalAlpha = a
    ctx.fillStyle = g
    ctx.fillRect(0, 0, W, H)
    ctx.globalAlpha = 1
  }
  // 极淡的品牌蓝光晕，避免白底显得平板
  glow(120, 120, 460, '#0A84FF', 0.07)
  glow(660, 220, 420, '#5E5CE6', 0.05)
  glow(375, 1240, 520, '#0A84FF', 0.05)

  // 圆角卡片
  const card = (x, y, w, h, r, fill, stroke) => {
    ctx.beginPath()
    ctx.moveTo(x + r, y)
    ctx.arcTo(x + w, y, x + w, y + h, r)
    ctx.arcTo(x + w, y + h, x, y + h, r)
    ctx.arcTo(x, y + h, x, y, r)
    ctx.arcTo(x, y, x + w, y, r)
    ctx.closePath()
    if (fill) {
      ctx.fillStyle = fill
      ctx.fill()
    }
    if (stroke) {
      ctx.strokeStyle = stroke
      ctx.lineWidth = 2
      ctx.stroke()
    }
  }

  const text = (str, x, y, { size = 22, color = '#1D1D1F', align = 'left', weight = '400', mono = false } = {}) => {
    ctx.fillStyle = color
    ctx.textAlign = align
    ctx.textBaseline = 'alphabetic'
    ctx.font = `${weight} ${size}px ${mono ? '"SF Mono", ui-monospace, Consolas, monospace' : '"PingFang SC","Microsoft YaHei",sans-serif'}`
    ctx.fillText(str, x, y)
  }

  const wrap = (str, x, y, maxW, lineH, size, color) => {
    ctx.font = `400 ${size}px "PingFang SC","Microsoft YaHei",sans-serif`
    ctx.fillStyle = color
    ctx.textAlign = 'left'
    const chars = String(str || '').split('')
    let line = ''
    let ly = y
    chars.forEach((c) => {
      if (ctx.measureText(line + c).width > maxW) {
        ctx.fillText(line, x, ly)
        line = c
        ly += lineH
      } else {
        line += c
      }
    })
    ctx.fillText(line, x, ly)
    return ly
  }

  // 顶部品牌
  text('校招全流程助手', 60, 92, { size: 26, color: '#0071E3', weight: '700' })
  text('INTERVIEW SCORECARD', 60, 126, { size: 18, color: '#6E6E73', mono: true })
  text('面试成绩单', 60, 186, { size: 44, color: '#1D1D1F', weight: '800' })

  // 主成绩卡
  card(50, 220, 650, 366, 32, '#FFFFFF', 'rgba(0,0,0,.08)')
  text('总分', 92, 296, { size: 22, color: '#52525B' })
  ctx.font = '800 108px "SF Mono", ui-monospace, Consolas, monospace'
  const scoreWidth = ctx.measureText(`${fmt(data.score)}`).width
  text(`${fmt(data.score)}`, 92, 420, { size: 108, color: '#1D1D1F', weight: '800', mono: true })
  text('/ 10', 92 + scoreWidth + 18, 420, {
    size: 32,
    color: '#6E6E73',
    weight: '600',
    mono: true,
  })
  text(`百分制 ${Math.round(Number(data.percent) || 0)} 分`, 92, 470, { size: 22, color: '#52525B' })

  // 等级徽标（canvas 读不到 CSS 变量，色值需与 global.css 的语义令牌保持一致）
  const gc = String(data.grade || '').charAt(0).toUpperCase()
  const gradeColor = gc === 'S' || gc === 'A' ? '#1A8348' : gc === 'B' ? '#0060DF' : gc === 'C' ? '#B25000' : '#D70015'
  ctx.beginPath()
  ctx.arc(586, 340, 76, 0, Math.PI * 2)
  ctx.fillStyle = '#F5F5F7'
  ctx.fill()
  ctx.strokeStyle = gradeColor
  ctx.lineWidth = 5
  ctx.stroke()
  text(String(data.grade || '—'), 586, 350, { size: 46, color: gradeColor, align: 'center', weight: '800' })
  text(String(data.grade_desc || '').slice(0, 10), 586, 396, {
    size: 18,
    color: '#52525B',
    align: 'center',
  })

  // 目标岗位 / 公司 / 日期
  card(50, 612, 650, 106, 24, '#FFFFFF', 'rgba(0,0,0,.08)')
  text('目标岗位', 82, 658, { size: 18, color: '#6E6E73' })
  text(String(data.job_title || '—').slice(0, 16), 82, 694, { size: 26, color: '#1D1D1F', weight: '700' })
  text('目标公司', 400, 658, { size: 18, color: '#6E6E73' })
  text(String(data.company_name || '—').slice(0, 12), 400, 694, { size: 26, color: '#1D1D1F', weight: '700' })

  // 雷达图缩略
  const dims = Array.isArray(data.dimensions) ? data.dimensions : []
  const cx = 375
  const cy = 950
  const R = 132
  if (dims.length) {
    const pt = (i, ratio) => {
      const a = -Math.PI / 2 + (i * 2 * Math.PI) / dims.length
      const r = R * Math.max(0, Math.min(1, ratio))
      return [cx + Math.cos(a) * r, cy + Math.sin(a) * r]
    }
    // 网格
    for (let ring = 1; ring <= 4; ring += 1) {
      ctx.beginPath()
      dims.forEach((_, i) => {
        const [x, y] = pt(i, ring / 4)
        if (i === 0) ctx.moveTo(x, y)
        else ctx.lineTo(x, y)
      })
      ctx.closePath()
      ctx.strokeStyle = 'rgba(0,0,0,.1)'
      ctx.lineWidth = 1.4
      ctx.stroke()
    }
    // 轴
    dims.forEach((_, i) => {
      const [x, y] = pt(i, 1)
      ctx.beginPath()
      ctx.moveTo(cx, cy)
      ctx.lineTo(x, y)
      ctx.strokeStyle = 'rgba(0,0,0,.07)'
      ctx.stroke()
    })
    // 得分多边形
    ctx.beginPath()
    dims.forEach((d, i) => {
      const [x, y] = pt(i, (Number(d.score) || 0) / 10)
      if (i === 0) ctx.moveTo(x, y)
      else ctx.lineTo(x, y)
    })
    ctx.closePath()
    const fill = ctx.createLinearGradient(0, cy - R, 0, cy + R)
    fill.addColorStop(0, 'rgba(10,132,255,.26)')
    fill.addColorStop(1, 'rgba(94,92,230,.1)')
    ctx.fillStyle = fill
    ctx.fill()
    ctx.strokeStyle = '#0A84FF'
    ctx.lineWidth = 3
    ctx.stroke()
    // 顶点 + 标签
    dims.forEach((d, i) => {
      const [x, y] = pt(i, (Number(d.score) || 0) / 10)
      ctx.beginPath()
      ctx.arc(x, y, 5, 0, Math.PI * 2)
      ctx.fillStyle = '#FFFFFF'
      ctx.fill()
      ctx.strokeStyle = '#0A84FF'
      ctx.lineWidth = 2.6
      ctx.stroke()
      const [lx, ly] = pt(i, 1.3)
      const a = -Math.PI / 2 + (i * 2 * Math.PI) / dims.length
      const anchor = Math.abs(Math.cos(a)) < 0.3 ? 'center' : Math.cos(a) > 0 ? 'left' : 'right'
      text(d.name || '', lx, ly - 4, { size: 19, color: '#52525B', align: anchor })
      text(fmt(d.score), lx, ly + 20, { size: 19, color: '#1D1D1F', align: anchor, weight: '700', mono: true })
    })
  }

  // 一句话点评（限制两行，避免与页脚重叠）
  text('面试官点评', 60, 1120, { size: 20, color: '#6E6E73' })
  const comment = String(data.comment || '继续加油，按提升计划练起来。')
  const shortComment = comment.length > 62 ? `${comment.slice(0, 62)}…` : comment
  wrap(shortComment, 60, 1156, 630, 31, 21, '#1D1D1F')

  // 底部
  text(`评分日期：${data.date || ''}`, 60, 1288, { size: 19, color: '#6E6E73', mono: true })
  text('练够 100 次，面试不慌', 690, 1288, { size: 19, color: '#0071E3', align: 'right', weight: '700' })
  text('校招全流程助手 · 数字人面试官 · 六维结构化评分', 375, 1320, {
    size: 18,
    color: '#6E6E73',
    align: 'center',
  })
}

export default function Report() {
  const { sessionId } = useParams()
  const nav = useNavigate()
  const [report, setReport] = useState(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [regenerating, setRegenerating] = useState(false)
  const [activeDim, setActiveDim] = useState('')
  const [openQuestions, setOpenQuestions] = useState({})
  const [checked, setChecked] = useState({})
  const [feedback, setFeedback] = useState(null)
  const [posterOpen, setPosterOpen] = useState(false)
  const [posterUrl, setPosterUrl] = useState('')
  const [posterLoading, setPosterLoading] = useState(false)
  const [exporting, setExporting] = useState(false)
  // 逐题评分反馈：题号 -> accurate|high|low（体验增强 §6）
  const [qFeedback, setQFeedback] = useState({})
  const canvasRef = useRef(null)
  const planKey = `aic_plan_${sessionId}`
  const planStartKey = `${planKey}_start`
  const planNotifyKey = `${planKey}_notify`
  /* 计划开始日（Day1）：首次打开本报告的那天，用来把「今天」置顶 */
  const [planStart, setPlanStart] = useState('')
  const [notifyPref, setNotifyPref] = useState('off')

  const load = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const data = await getReport(sessionId)
      setReport(data)
      // 回填已提交过的逐题反馈，刷新后仍能看到自己的选择
      const seeded = {}
      for (const q of data?.questions || []) {
        if (q?.feedback_verdict) seeded[q.index] = q.feedback_verdict
      }
      setQFeedback(seeded)
    } catch (err) {
      setError(err?.message || '报告加载失败')
    } finally {
      setLoading(false)
    }
  }, [sessionId])

  useEffect(() => {
    load()
  }, [load])

  /* 7 天计划勾选状态（localStorage 持久化） */
  useEffect(() => {
    setChecked(loadLocal(planKey, {}))
    setNotifyPref(loadLocal(planNotifyKey, 'off'))
    // 首次打开本报告的那天就是 Day1；之后每次打开都以它为基准算「今天第几天」。
    const saved = loadLocal(planStartKey, '')
    if (saved) {
      setPlanStart(saved)
      return
    }
    const now = new Date()
    const iso = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(
      now.getDate()
    ).padStart(2, '0')}`
    setPlanStart(iso)
    saveLocal(planStartKey, iso)
  }, [planKey, planStartKey, planNotifyKey])

  const toggleTask = (key) => {
    setChecked((prev) => {
      const next = { ...prev, [key]: !prev[key] }
      saveLocal(planKey, next)
      return next
    })
  }

  const onRegenerate = async () => {
    setRegenerating(true)
    try {
      const data = await generateReport(sessionId)
      setReport(data)
      // 新计划的任务下标会变，清掉旧勾选避免残留
      setChecked({})
      saveLocal(planKey, {})
      notify('报告已重新生成', 'success')
    } catch {
      /* 已弹 Toast */
    } finally {
      setRegenerating(false)
    }
  }

  const onFeedback = async (helpful) => {
    try {
      await reportFeedback(sessionId, helpful)
      setFeedback(helpful)
      notify('感谢反馈，我们会持续优化评分', 'success')
    } catch {
      /* 已弹 Toast */
    }
  }

  /** 逐题评分反馈（体验增强 §6）：写后端 + 本地立即回显 */
  const onQuestionFeedback = async (index, verdict) => {
    const prev = qFeedback[index]
    // 乐观更新：反馈是轻交互，等接口回来再变色会显得没反应
    setQFeedback((p) => ({ ...p, [index]: verdict }))
    try {
      await reportQuestionFeedback(sessionId, index, verdict)
      notify('已记录，感谢反馈，我们会持续校准评分', 'success')
    } catch {
      // 失败回滚，避免界面显示一个其实没存上的选择（已在 client 里弹过 Toast）
      setQFeedback((p) => ({ ...p, [index]: prev || '' }))
    }
  }

  /** 目前只有 Markdown 一种导出格式，因此 MIME 固定 */
  const download = (content, filename) => {
    const blob = new Blob([content], { type: 'text/markdown;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    setTimeout(() => URL.revokeObjectURL(url), 1500)
  }

  const onExport = async () => {
    setExporting(true)
    try {
      const data = await exportReport(sessionId, 'markdown')
      download(data?.content || '', data?.filename || `面试报告_${sessionId}.md`)
      notify('Markdown 报告已开始下载', 'success')
    } catch {
      /* 已弹 Toast */
    } finally {
      setExporting(false)
    }
  }

  const onPoster = async () => {
    setPosterLoading(true)
    try {
      const data = await getShare(sessionId)
      const poster = data?.poster_data || {}
      const canvas = canvasRef.current || document.createElement('canvas')
      drawPoster(canvas, poster)
      setPosterUrl(canvas.toDataURL('image/png'))
      setPosterOpen(true)
    } catch {
      notify('海报生成失败，可稍后重试', 'warn')
    } finally {
      setPosterLoading(false)
    }
  }

  const dimsForRadar = useMemo(() => {
    const dims = report?.dimensions || []
    return dims.map((d) => ({ key: d.key, name: d.name, score: d.score }))
  }, [report])

  const activeDimension = useMemo(
    () => (report?.dimensions || []).find((d) => d.key === activeDim || d.name === activeDim),
    [report, activeDim]
  )

  /* ==========================================================================
     派生值 + hooks：**必须全部位于下面的早退 return 之前**
     ---------------------------------------------------------------------------
     这是一个踩过的坑：报告页有「加载中」「加载失败」两个提前 return 的分支，
     而 7 天计划闭环（§10）又带来了 useMemo/useCallback/useEffect。一旦这些 hooks
     被写在提前 return 之后，首次渲染（loading=true）会跳过它们，数据到达后的第二次
     渲染才调用 —— React 发现「本次渲染的 hooks 比上次多」，直接抛错；
     页面又没有错误边界，于是整棵树被卸载：**白屏、毫无反应**，控制台只有一行报错。
     所以：hooks 一律前置；被 hooks 依赖的值也必须先算完（否则依赖数组会撞上 TDZ）。
     同类写法由 .regress/check_hooks.js 静态检查，避免再次回归。
     ========================================================================== */
  const ov = report?.overview || {}
  const meta = report?.meta || {}
  const progress = report?.progress || {}
  const bench = report?.benchmark || {}
  const retakeTotal = Number(report?.retake_count) || 0
  const hintTotal = Number(report?.hint_count) || 0
  const independent = Number(report?.independent_answer_rate)
  const days = report?.plan7days || []
  // 只统计「当前这份计划」里的任务勾选：
  // 重新生成报告后任务数可能变少，而 localStorage 里仍留着旧 key，
  // 直接数 checked 会出现「已完成 5/4」这种自相矛盾的进度。
  const planKeys = new Set(
    days.flatMap((d) => (d.tasks || []).map((_, i) => `${d.day}-${i}`))
  )
  const planTotal = planKeys.size
  const planDone = Object.entries(checked).filter(([k, v]) => v && planKeys.has(k)).length

  /* ==========================================================================
     体验增强 §10：7 天计划的执行闭环
     完成度环 + 今日置顶 + 一键开练 + 次日提醒 + 一周复盘
     ========================================================================== */
  const planPct = planTotal ? Math.round((planDone / planTotal) * 100) : 0

  /**
   * 「今天是第几天」：以计划开始日（首次打开本报告的日期）为 Day1。
   * 用本地日期差而不是"当前时间戳"，跨时区/跨零点都不会算错。
   */
  const todayIndex = useMemo(() => {
    if (!planStart) return 1
    const start = new Date(`${planStart}T00:00:00`)
    if (Number.isNaN(start.getTime())) return 1
    const now = new Date()
    const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
    const diff = Math.floor((today - start) / 86400000) + 1
    return Math.min(7, Math.max(1, diff))
  }, [planStart])

  /** 今日任务置顶：把今天那一天挪到最前，其余保持原顺序 */
  const orderedDays = useMemo(() => {
    if (days.length <= 1) return days
    const idx = days.findIndex((d) => Number(d.day) === todayIndex)
    if (idx <= 0) return days
    return [days[idx], ...days.slice(0, idx), ...days.slice(idx + 1)]
  }, [days, todayIndex])

  /**
   * 主任务的「立即开练」目标。
   *
   * 只在**真的存在对应去处**时才给按钮：拿不到落点的任务不硬塞一个假装能点的入口，
   * 否则就是"点了没反应"的反面教材。匹配基于任务标题+详情的真实关键词。
   */
  const taskAction = useCallback((task) => {
    const text = `${task?.title || ''}${task?.detail || ''}`
    if (/错题|复盘错/.test(text)) return { route: '/my-interviews?tab=wrongbook', label: '去错题本' }
    if (/简历|技能|关键词|项目描述/.test(text)) return { route: '/resume-optimizer', label: '去改简历' }
    if (/面试|模拟|模拟练习|重练/.test(text)) return { route: '/setup', label: '立刻开一场' }
    return null
  }, [])

  /* ---- 次日提醒：浏览器通知（拒绝即静默，绝不反复打扰）---- */
  useEffect(() => {
    if (!planStart || !planTotal) return
    if (typeof window === 'undefined' || !('Notification' in window)) return
    if (notifyPref !== 'on' || Notification.permission !== 'granted') return
    const left = planTotal - planDone
    if (left <= 0) return
    const todayKey = `aic_plan_notified_${sessionId}_${planStart}_${todayIndex}`
    // 同一天只提醒一次：刷新页面不该重复弹通知
    if (loadLocal(todayKey, false)) return
    try {
      new Notification('今天的面试练习还没做完', {
        body: `Day ${todayIndex} 还有 ${left} 个任务，约 ${days
          .find((d) => Number(d.day) === todayIndex)
          ?.tasks?.reduce((n, t) => n + (t.duration_min || 30), 0) || 30} 分钟`,
      })
      saveLocal(todayKey, true)
    } catch {
      /* 通知失败不影响页面 */
    }
  }, [planStart, planTotal, planDone, notifyPref, todayIndex, sessionId, days])

  /* ---------------- 加载 / 错误态 ---------------- */
  if (loading) {
    return (
      <div className="container">
        <div className="page-head">
          <div>
            <h1 className="page-title">面试报告</h1>
            <p className="page-sub">正在生成六维评分报告…</p>
          </div>
        </div>

        {/* 生成过程可视化：阶段清单 + 真实耗时 + 当前子步骤。
            拿不到进度（多进程部署 / 尚未开始）时自动退化为不确定进度条，不显示假百分比。 */}
        <ReportProgress sessionId={sessionId} active variant="full" />

        <div className="card mt-16">
          <div className="row gap-12" style={{ alignItems: 'center' }}>
            <div className="skeleton" style={{ width: 140, height: 140, borderRadius: '50%' }} />
            <div className="grow">
              <div className="skeleton sk-line sk-line--60" />
              <div className="skeleton sk-line" />
              <div className="skeleton sk-line sk-line--40" />
            </div>
          </div>
          <div className="skeleton" style={{ height: 240, marginTop: 16 }} />
        </div>
        <div className="center muted mt-16">
          首次生成会完整跑一遍六维评分，请留在本页；完成后报告会自动显示。
        </div>
      </div>
    )
  }

  if (error || !report) {
    return (
      <div className="container">
        <div className="card mt-16">
          <div className="empty">
            <IconInfo size={28} style={{ color: 'var(--text-mute)' }} />
            <div className="empty-title">报告加载失败</div>
            <div className="empty-desc">{error || '未获取到报告数据'}</div>
            <div className="row gap-12 mt-16" style={{ justifyContent: 'center' }}>
              <button className="btn btn--primary" onClick={load}>
                重试
              </button>
              <button className="btn btn--ghost" onClick={() => nav('/setup')}>
                再练一次
              </button>
            </div>
          </div>
        </div>
      </div>
    )
  }

  /* 计划周期是否结束（用完 7 天或全部勾完）：纯派生值，供 JSX 判断是否出「一周复盘」 */
  const periodEnded = planTotal > 0 && (todayIndex >= 7 || planPct === 100)

  const enableNotify = async () => {
    if (typeof window === 'undefined' || !('Notification' in window)) {
      notify('当前浏览器不支持桌面通知，可以每天回到本页打勾', 'warn', 3600)
      return
    }
    if (Notification.permission === 'denied') {
      // 明确告知而不是静默失败：用户会疑惑"我点了为什么没反应"
      notify('浏览器已拒绝通知权限，请在站点设置里允许后再试', 'warn', 4000)
      return
    }
    try {
      const perm = await Notification.requestPermission()
      if (perm === 'granted') {
        setNotifyPref('on')
        saveLocal(planNotifyKey, 'on')
        notify('已开启：每次打开本站时会提醒你当天还没做完的任务', 'success', 3600)
      } else {
        setNotifyPref('off')
        saveLocal(planNotifyKey, 'off')
      }
    } catch {
      /* 忽略 */
    }
  }

  return (
    <div className="container">
      <div className="page-head">
        <div>
          <h1 className="page-title">面试报告</h1>
          <p className="page-sub">
            {meta.scored_at ? `评分时间 ${String(meta.scored_at).replace('T', ' ').slice(0, 16)} · ` : ''}
            评分方式：
            {meta.scoring_mode === 'llm' ? '大模型评分' : meta.scoring_mode === 'hybrid' ? '混合评分' : '规则引擎评分'}
          </p>
        </div>
        <button className="btn btn--ghost btn--sm" onClick={onRegenerate} disabled={regenerating}>
          {regenerating ? <span className="spinner spinner--primary" /> : <IconRefresh size={16} />}
          {regenerating ? '生成中…' : '重新生成'}
        </button>
      </div>

      {/* 重新生成时的进度：保留旧报告可见，用紧凑横幅说明跑到哪一步了 */}
      {regenerating && <ReportProgress sessionId={sessionId} active variant="compact" />}

      {/* ================= AI 生成标识（体验增强 §6，首屏可见）=================
          产品底线：不能让 AI 评分看起来像真人 HR 的判断 */}
      <div className="ai-disclaimer" role="note">
        <IconBulb size={16} />
        <span className="grow">
          {report.ai_disclaimer ||
            '本报告由 AI 生成，仅供参考，不构成录用判断。每个维度下方可展开查看评分依据。'}
        </span>
      </div>

      {/* ================= 诚实披露：重录 / 依赖提示 ================= */}
      {(retakeTotal > 0 || hintTotal > 0) && (
        <div className="alert alert--info mb-12">
          <IconInfo size={17} />
          <div className="grow">
            <div>
              本场如实记录：
              {retakeTotal > 0 ? ` 重录 ${retakeTotal} 次` : ''}
              {retakeTotal > 0 && hintTotal > 0 ? ' · ' : ''}
              {hintTotal > 0 ? ` 使用思路提示 ${hintTotal} 题` : ''}
              {Number.isFinite(independent) ? `，独立作答率 ${Math.round(independent * 100)}%` : ''}。
            </div>
            <div className="mute-2 mt-8" style={{ fontSize: 12 }}>
              重录与提示都不扣分，只是让分数更可解释；「独立作答率」= 未用提示的作答占比。
            </div>
          </div>
        </div>
      )}

      {/* ================= 单题特训的前后对比（体验增强 §9）=================
          只练了一个环节的微面试，要把"这次练的维度比上次好了多少"直接摆出来，
          否则用户跑完 3 题不知道为什么值得。 */}
      {report.retry_section && (() => {
        const dim = (report.dimensions || []).find((d) => d.name === report.retry_dimension)
        const after = dim ? Number(dim.score) : 0
        const delta = dim ? Number(dim.improved) : 0
        const before = Number((after - delta).toFixed(1))
        return (
          <div className="alert alert--info mb-12">
            <IconTarget size={17} />
            <div className="grow">
              <div>
                本场是「{report.retry_section}」的单题特训
                {dim ? ` · ${dim.name}：上次 ${fmt(before)} → 本次 ${fmt(after)}` : ''}
                {dim && delta !== 0 ? `（${delta > 0 ? '+' : ''}${fmt(delta)}）` : ''}
              </div>
              <div className="mute-2 mt-8" style={{ fontSize: 12 }}>
                特训场次只练一个环节，分数只代表这一维；完整结论仍以常规面试的六维报告为准。
              </div>
            </div>
          </div>
        )
      })()}

      {/* ================= 压力面试模式标注（体验增强 §14）=================
          压力面会改变节奏与题型，分数与其他场次不完全可比，必须说清楚。 */}
      {report.pressure_mode && (
        <div className="alert alert--warn mb-12">
          <IconWarn size={17} />
          <div className="grow">
            <div>
              本场为<strong>压力面试模式</strong>：追问更密、偏压力测试题。
              {report.industry ? `题目优先取自「${report.industry}」行业题库。` : ''}
            </div>
            <div className="mute-2 mt-8" style={{ fontSize: 12 }}>
              压力面下的分数受节奏影响，建议与「常规模式」的场次分开比较；抗压表现本身也是有价值的训练目标。
            </div>
          </div>
        </div>
      )}

      {meta.persist_failed && (
        <div className="alert alert--warn mb-12">
          <IconWarn size={17} />
          <div>
            <div>这份报告未能保存到系统，刷新或重新打开本页会重新评分，分数可能有差异。</div>
            <div className="mute-2 mt-8" style={{ fontSize: 12 }}>
              可以先「导出 Markdown / JSON」留存当前结果，再点右上角「重新生成」重试。
            </div>
          </div>
        </div>
      )}

      {(meta.cheating_flags || []).length > 0 && (
        <div className="alert alert--warn mb-12">
          <IconWarn size={17} />
          <div>
            {meta.cheating_flags.slice(0, 3).map((f, i) => (
              <div key={i}>{f}</div>
            ))}
          </div>
        </div>
      )}

      {/* ================= 板块1 总览 ================= */}
      <section className="card report-overview fade-up">
        <div className="ov-left">
          <div className="ov-score">
            <span className="mono ov-score-num">{fmt(ov.score)}</span>
            <span className="ov-score-unit">/ 10</span>
          </div>
          <div className="row gap-8 mt-8">
            <span className={`grade ${gradeClass(ov.grade)}`}>{ov.grade || '—'}</span>
            <span className="chip">{ov.grade_desc || ''}</span>
          </div>
          <p className="ov-comment">{ov.one_line_comment || '—'}</p>
          <div className="ov-meta">
            <span className="chip">百分制 {Math.round(Number(ov.percent) || 0)} 分</span>
            <span className="chip chip--ok">模拟 Offer 概率 {ov.offer_probability || '—'}</span>
            <span className="chip">时长 {ov.duration_minutes || 0} 分钟</span>
            <span className="chip">
              题量 {ov.answered_count || 0}/{ov.question_count || 0}
            </span>
          </div>
        </div>
        <div className="ov-ring">
          <ScoreRing
            value={Number(ov.percent) || 0}
            size={148}
            label="综合得分率"
            sub={`等级 ${ov.grade || '—'}`}
          />
        </div>

        {/* 基准刻度（体验增强 §6）：把本场分数放到历史分布上，让它有参照物。
            样本不足时后端给 available=false + 原因，这里如实展示，不编造分布。 */}
        <div className="ov-bench">
          {bench.available ? (
            <>
              <div className="row row--between row--wrap gap-8">
                <span className="block-label" style={{ margin: 0 }}>
                  <IconTrendUp size={15} /> 同岗位历史分布
                </span>
                <span className="mute-2" style={{ fontSize: 12 }}>
                  本场 {fmt(ov.score)} · {bench.note}
                </span>
              </div>
              <div className="bench-scale" role="img" aria-label={`本场 ${fmt(ov.score)} 分，历史 P25 ${fmt(bench.p25)}、P50 ${fmt(bench.p50)}、P75 ${fmt(bench.p75)}`}>
                {BENCH_MARKS.map((m) => (
                  <div className="bench-mark" key={m.key}>
                    <span className="bench-dot" />
                    <span className="bench-val mono">{fmt(bench[m.key])}</span>
                    <span className="bench-label">{m.label}</span>
                  </div>
                ))}
                <div
                  className="bench-mine"
                  style={{
                    left: `${Math.max(0, Math.min(100, (Number(ov.score) || 0) * 10))}%`,
                  }}
                  title={`本场 ${fmt(ov.score)} 分`}
                >
                  <span className="bench-mine-tag">你</span>
                </div>
              </div>
            </>
          ) : (
            <div className="mute-2" style={{ fontSize: 12 }}>
              {bench.note || '暂无可用的历史基准（样本不足）'}
            </div>
          )}
        </div>
      </section>

      {/* ================= 板块2 六维雷达图 ================= */}
      <SectionCard
        index="2"
        title="六维能力雷达图"
        desc="实线为你的得分，虚线为目标岗位平均分；点击维度查看点评与原文证据"
        className="mt-16"
      >
        <div className="radar-wrap">
          <RadarChart
            dimensions={dimsForRadar}
            target={report.radar_target || {}}
            active={activeDim}
            onSelect={(k) => setActiveDim((prev) => (prev === k ? '' : k))}
          />
        </div>

        {activeDimension ? (
          <div className="sub-card mt-12 fade-up">
            <div className="row row--between">
              <div className="card-title">
                <span>{activeDimension.name}</span>
                <span className="chip chip--accent mono">权重 {Math.round((activeDimension.weight || 0) * 100)}%</span>
                <span className="chip mono">得分 {fmt(activeDimension.score)}</span>
                {Number(activeDimension.improved) !== 0 && (
                  <span className={`chip ${Number(activeDimension.improved) > 0 ? 'chip--ok' : 'chip--danger'}`}>
                    较上次 {Number(activeDimension.improved) > 0 ? '+' : ''}
                    {fmt(activeDimension.improved)}
                  </span>
                )}
              </div>
              <button className="link-btn" onClick={() => setActiveDim('')}>
                收起
              </button>
            </div>
            <p className="dim-comment">{activeDimension.comment || '暂无点评'}</p>

            {/* 评分依据：这个分数具体看了哪些可数事实（与评分引擎同一套口径） */}
            {(activeDimension.basis || []).length > 0 && (
              <>
                <div className="block-label">
                  <IconTarget size={15} /> 评分依据（这一维实际看了什么）
                </div>
                <ul className="basis-list">
                  {activeDimension.basis.map((b, i) => (
                    <li key={i}>{b}</li>
                  ))}
                </ul>
              </>
            )}

            {(activeDimension.evidence || []).length > 0 && (
              <>
                <div className="block-label">回答原文证据</div>
                <ul className="quote-list">
                  {activeDimension.evidence.map((e, i) => (
                    <li key={i}>“{e}”</li>
                  ))}
                </ul>
              </>
            )}

            {/* 评价锚点：把抽象分数具体化成「什么样算 8 分」 */}
            {(activeDimension.anchors || []).length > 0 && (
              <>
                <div className="block-label">
                  <IconInfo size={15} /> 评价锚点
                </div>
                {(activeDimension.anchors || []).map((a, i) => (
                  <p className="anchor-line" key={i}>
                    <span className="chip chip--accent">{a.level}</span>
                    <span className="mute-2">（{fmt(a.score)} 分大概是这样）</span>
                    <br />
                    {a.example}
                  </p>
                ))}
              </>
            )}

            {/* 该维度的历史基准：样本不足时如实说明，不拿总分刻度冒充 */}
            <div className="dim-bench">
              {activeDimension.benchmark?.available ? (
                <>
                  <span className="mute-2" style={{ fontSize: 12 }}>
                    同岗位历史：P25 {fmt(activeDimension.benchmark.p25)} · P50{' '}
                    {fmt(activeDimension.benchmark.p50)} · P75 {fmt(activeDimension.benchmark.p75)}
                    （{activeDimension.benchmark.sample_size} 份）
                  </span>
                  <span className="mute-2" style={{ fontSize: 12 }}>
                    本场 {fmt(activeDimension.score)} 分
                    {Number(activeDimension.score) >= Number(activeDimension.benchmark.p75)
                      ? '，高于四分之三的历史样本'
                      : Number(activeDimension.score) >= Number(activeDimension.benchmark.p50)
                        ? '，高于历史中位数'
                        : Number(activeDimension.score) >= Number(activeDimension.benchmark.p25)
                          ? '，低于历史中位数但高于 P25'
                          : '，低于四分之三的历史样本，是当前最该补的一维'}
                  </span>
                </>
              ) : (
                <span className="mute-2" style={{ fontSize: 12 }}>
                  {activeDimension.benchmark?.note || '该维度暂无历史基准'}
                </span>
              )}
            </div>
          </div>
        ) : (
          <div className="dim-brief mt-12">
            {(report.dimensions || []).map((d) => (
              <button
                key={d.key}
                className={`dim-chip ${dimClass(d.key)} ${activeDim === d.key ? 'on' : ''}`}
                onClick={() => setActiveDim(d.key)}
              >
                <i className="dim-dot" />
                <span>{d.name}</span>
                <b className="mono">{fmt(d.score)}</b>
              </button>
            ))}
          </div>
        )}
      </SectionCard>

      {/* ================= 板块3 逐题分析 ================= */}
      <SectionCard
        index="3"
        title="逐题详细分析"
        desc="每题做得好 / 待改进 / 参考答案要点，可展开查看你的回答原文"
        className="mt-16"
      >
        <div className="q-list">
          {(report.questions || []).map((q) => {
            const open = !!openQuestions[q.index]
            return (
              <div className={`q-item ${q.too_short ? 'q-item--short' : ''}`} key={q.index}>
                <button
                  className="q-head"
                  onClick={() => setOpenQuestions((p) => ({ ...p, [q.index]: !p[q.index] }))}
                >
                  <span className="q-idx mono">{q.index}</span>
                  <span className="grow">
                    <span className="q-line">
                      <span className="chip chip--primary">{q.section || '面试题'}</span>
                      {q.focus && <span className="chip">考察：{q.focus}</span>}
                      {/* 四种状态必须可区分：未作答 / 重录 / 依赖提示 / 单纯低分 */}
                      {q.skipped && <span className="chip chip--warn">未作答</span>}
                      {!q.skipped && q.retake_count > 0 && (
                        <span className="chip">重录 {q.retake_count} 次</span>
                      )}
                      {!q.skipped && q.hint_used && <span className="chip">用了思路提示</span>}
                      {q.too_short && <span className="chip chip--warn">回答过短，评分仅供参考</span>}
                    </span>
                    <span className="q-title">{q.question}</span>
                  </span>
                  {/* 未作答不给分数：跳过得 1.5 分和「答得差」得 1.5 分是两件事 */}
                  <span className="q-score mono">{q.skipped ? '—' : fmt(q.score)}</span>
                  <span className="q-arrow">{open ? '▲' : '▼'}</span>
                </button>

                {open && (
                  <div className="q-body fade-up">
                    <div className="q-cols">
                      <div>
                        <div className="block-label ok">
                        <IconCheckCircle size={15} /> 做得好
                      </div>
                        <ul className="dot-list dot-list--ok">
                          {(q.good || []).map((g, i) => (
                            <li key={i}>{g}</li>
                          ))}
                          {!(q.good || []).length && <li className="mute-2">暂无</li>}
                        </ul>
                      </div>
                      <div>
                        <div className="block-label warn">
                          <IconSparkle size={15} /> 待改进
                        </div>
                        <ul className="dot-list dot-list--warn">
                          {(q.improve || []).map((g, i) => (
                            <li key={i}>{g}</li>
                          ))}
                          {!(q.improve || []).length && <li className="mute-2">暂无</li>}
                        </ul>
                      </div>
                    </div>
                    {q.reference && (
                      <>
                        <div className="block-label">
                          <IconDoc size={15} /> 参考答案要点
                        </div>
                        <p className="ref-text">{q.reference}</p>
                      </>
                    )}

                    {/* 评分依据的原文出处：结论 -> 点开就看到是哪句话支撑的 */}
                    {(q.evidence_spans || []).length > 0 && (
                      <>
                        <div className="block-label">
                          <IconTarget size={15} /> 支撑本题评分的原文片段
                        </div>
                        <ul className="span-list">
                          {q.evidence_spans.map((s, i) => (
                            <li key={i}>
                              <mark>{s}</mark>
                            </li>
                          ))}
                        </ul>
                      </>
                    )}

                    {/* 口播指标：与六维评分并排展示的**事实**，不参与打分 */}
                    {q.speech && (
                      <div className="q-speech">
                        <span className="chip">
                          <IconClock size={13} className="inline-ic" /> 语速{' '}
                          {Math.round(Number(q.speech.chars_per_minute) || 0)} 字/分
                        </span>
                        <span className="chip">
                          填充词 {q.speech.filler_count || 0} 次
                          {(q.speech.filler_words || []).length > 0
                            ? `「${q.speech.filler_words.slice(0, 3).join('/')}」`
                            : ''}
                        </span>
                        {q.speech.note && <span className="mute-2">{q.speech.note}</span>}
                      </div>
                    )}

                    <div className="block-label">
                      <IconUser size={15} /> 你的回答原文（{q.answer_seconds || 0} 秒）
                    </div>
                    <p className="answer-text">{q.answer || '（本题未作答）'}</p>

                    {/* 逐题评分反馈：让用户能质疑，反馈用于校准评分 Prompt */}
                    <div className="q-feedback">
                      <span className="mute-2" style={{ fontSize: 12 }}>
                        这个分数准吗？
                      </span>
                      {VERDICT_OPTIONS.map((v) => {
                        const Icon = v.icon
                        const on = qFeedback[q.index] === v.key
                        return (
                          <button
                            type="button"
                            key={v.key}
                            className={`btn btn--sm ${on ? 'btn--soft' : 'btn--ghost'}`}
                            aria-pressed={on}
                            onClick={() => onQuestionFeedback(q.index, v.key)}
                          >
                            <Icon size={15} /> {v.label}
                          </button>
                        )
                      })}
                      {qFeedback[q.index] && (
                        <span className="mute-2" style={{ fontSize: 12 }}>
                          已反馈，感谢——这类样本会用来校准评分
                        </span>
                      )}
                    </div>
                  </div>
                )}
              </div>
            )
          })}
          {!(report.questions || []).length && (
            <div className="empty">
              <div className="empty-desc">本次面试没有可分析的题目</div>
            </div>
          )}
        </div>
      </SectionCard>

      {/* ================= 板块4 核心优势 ================= */}
      <SectionCard index="4" title="核心优势" desc="本次面试中最值得在后续面试里放大的 3 点" className="mt-16">
        <div className="strength-grid">
          {(report.strengths || []).map((s, i) => (
            <div className="strength-card" key={i}>
              <div className="strength-no">0{i + 1}</div>
              <div className="strength-title">{s.title}</div>
              <p className="strength-detail">{s.detail}</p>
              {s.evidence && <div className="quote">“{s.evidence}”</div>}
            </div>
          ))}
          {!(report.strengths || []).length && (
            <div className="empty">
              <div className="empty-desc">暂无优势项数据</div>
            </div>
          )}
        </div>
      </SectionCard>

      {/* ================= 板块5 待改进项 ================= */}
      <SectionCard
        index="5"
        title="待改进项"
        desc="每条都给出「问题表现 + 3 条具体改进方法 + 推荐资源」"
        className="mt-16"
      >
        <div className="imp-list">
          {(report.improvements || []).map((it, i) => {
            const pm = priorityMeta[it.priority] || priorityMeta.medium
            return (
              <div className={`imp-item ${pm.cls}`} key={i}>
                <div className="row row--between row--wrap">
                  <div className="row gap-8">
                    <span className={`chip ${it.priority === 'high' ? 'chip--danger' : it.priority === 'low' ? 'chip--ok' : 'chip--warn'}`}>
                      {pm.label}
                    </span>
                    <span className={`chip chip--dim ${dimClass(it.dimension)}`}>{it.dimension}</span>
                  </div>
                </div>
                <p className="imp-problem">{it.problem}</p>
                {it.evidence && <div className="quote">“{it.evidence}”</div>}

                <div className="imp-actions">
                  <div className="imp-actions-title">
                    <IconSparkle size={15} /> 3 条改进方法
                  </div>
                  <ol>
                    {(it.actions || []).map((a, idx) => (
                      <li key={idx}>
                        <span className="imp-step mono">{idx + 1}</span>
                        <span>{a}</span>
                      </li>
                    ))}
                  </ol>
                </div>

                {(it.resources || []).length > 0 && (
                  <div className="imp-res">
                    <span className="block-label" style={{ margin: 0 }}>
                      推荐资源
                    </span>
                    <div className="tag-list">
                      {it.resources.map((r, idx) => (
                        <span className="chip chip--accent" key={idx}>
                          {r}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )
          })}
          {!(report.improvements || []).length && (
            <div className="empty">
              <div className="empty-desc">本次没有明显短板，继续保持！</div>
            </div>
          )}
        </div>
      </SectionCard>

      {/* ================= 板块6 7 天计划 ================= */}
      <SectionCard
        index="6"
        title="个性化 7 天提升计划"
        desc="每天 2-3 个可执行任务，做完打勾（本地自动保存进度）"
        className="mt-16"
        right={
          <span className={`chip mono ${planPct >= 100 ? 'chip--ok' : ''}`}>
            已完成 {planDone}/{planTotal}
          </span>
        }
      >
        {/* 完成度环 + 今日提醒（体验增强 §10：计划的执行闭环） */}
        {planTotal > 0 && (
          <div className="plan-progress">
            <ScoreRing value={planPct} size={96} label="计划完成度" sub={`Day ${todayIndex}/7`} />
            <div className="grow">
              <div className="plan-progress-line">
                {planPct >= 100
                  ? '7 天计划已全部完成，可以做一次周期复盘并开下一轮。'
                  : `今天是第 ${todayIndex} 天，完成度 ${planPct}%。任务已按今天置顶。`}
              </div>
              <div className="mute-2" style={{ fontSize: 12, lineHeight: 1.6 }}>
                勾选进度存在本机；重开报告不会丢。提醒依赖浏览器通知，关闭页面后无法推送，
                因此只在**你打开本站时**提示当天未完成的任务。
              </div>
              <div className="row gap-8 row--wrap mt-8">
                {notifyPref === 'on' ? (
                  <span className="chip chip--ok">已开启任务提醒</span>
                ) : (
                  <button className="btn btn--ghost btn--sm" onClick={enableNotify}>
                    <IconClock size={15} /> 开启任务提醒
                  </button>
                )}
                {notifyPref === 'on' && (
                  <button
                    className="btn btn--ghost btn--sm"
                    onClick={() => {
                      setNotifyPref('off')
                      saveLocal(planNotifyKey, 'off')
                      notify('已关闭任务提醒', 'info')
                    }}
                  >
                    关闭提醒
                  </button>
                )}
              </div>
            </div>
          </div>
        )}

        {/* 一周复盘：周期结束或全部完成时自动生成（纯本地计算，不额外调模型） */}
        {periodEnded && (
          <div className="plan-review">
            <div className="block-label">
              <IconTrendUp size={15} /> 一周复盘
            </div>
            <ul className="dot-list">
              <li>
                完成率 <b className="mono">{planPct}%</b>（{planDone}/{planTotal} 个任务，
                {planPct >= 80 ? '执行得很稳' : planPct >= 50 ? '过半了，后半程可以更紧凑' : '偏低，建议把每天任务减到 2 个再坚持'}
                ）
              </li>
              {(() => {
                const best = [...(report.dimensions || [])]
                  .filter((d) => Number(d.improved) > 0)
                  .sort((a, b) => Number(b.improved) - Number(a.improved))[0]
                return best ? (
                  <li>
                    提升最明显：<b>{best.name}</b> 较上次 +{fmt(best.improved)} 分，说明这条路径有效，下一轮继续按它练。
                  </li>
                ) : (
                  <li>本周期没有可比的历史维度分数，下一轮跑完一场常规面试就能看到变化。</li>
                )
              })()}
              {(() => {
                const weak = [...(report.dimensions || [])].sort(
                  (a, b) => Number(a.score) - Number(b.score)
                )[0]
                return weak ? (
                  <li>
                    下一周期建议：优先补 <b>{weak.name}</b>（本次 {fmt(weak.score)} 分）。
                    可以在错题本里对相关题做 3 题特训，比再跑整场更省时间。
                  </li>
                ) : null
              })()}
            </ul>
          </div>
        )}

        <div className="plan-grid">
          {orderedDays.map((day) => {
            const isToday = Number(day.day) === todayIndex && !periodEnded
            return (
              <div className={`plan-day${isToday ? ' plan-day--today' : ''}`} key={day.day}>
                <div className="plan-day-head">
                  <span className="plan-day-no mono">Day {day.day}</span>
                  <span className="plan-day-theme">{day.theme}</span>
                  {isToday && <span className="chip chip--primary">今天</span>}
                </div>
                <div className="plan-tasks">
                  {(day.tasks || []).map((t, i) => {
                    const key = `${day.day}-${i}`
                    const on = !!checked[key]
                    const action = taskAction(t)
                    return (
                      <div className={`plan-task ${on ? 'on' : ''}`} key={key}>
                        <label className="plan-task-main">
                          <input type="checkbox" checked={on} onChange={() => toggleTask(key)} />
                          <span className="plan-check">{on ? '✓' : ''}</span>
                          <span className="grow">
                            <span className="plan-task-top">
                              <span
                                className={`chip ${
                                  t.type === '知识补充'
                                    ? 'chip--primary'
                                    : t.type === '模拟练习'
                                      ? 'chip--accent'
                                      : 'chip--ok'
                                }`}
                              >
                                {t.type}
                              </span>
                              <span className="plan-task-title">{t.title}</span>
                            </span>
                            <span className="plan-task-detail">{t.detail}</span>
                            <span className="plan-task-time mono">
                              <IconClock size={13} className="inline-ic" />
                              {t.duration_min || 30} 分钟
                            </span>
                          </span>
                        </label>
                        {/* 立即开练：只在真有落点时出现，不硬塞点不动的按钮 */}
                        {action && (
                          <button
                            className="btn btn--ghost btn--sm plan-task-go"
                            onClick={() => nav(action.route)}
                          >
                            {action.label}
                          </button>
                        )}
                      </div>
                    )
                  })}
                </div>
              </div>
            )
          })}
          {!(report.plan7days || []).length && (
            <div className="empty">
              <div className="empty-desc">暂无提升计划</div>
            </div>
          )}
        </div>
      </SectionCard>

      {/* ================= 板块7 历史进步 ================= */}
      <SectionCard index="7" title="历史进步追踪" desc="你的分数变化曲线与进步最快的维度" className="mt-16">
        <ScoreTrend
          items={progress.history || []}
          ariaLabel="历史得分曲线"
          emptyTitle="还没有历史曲线"
          emptyDesc="完成第 2 次模拟面试后，这里会显示你的分数变化曲线。"
        />
        <div className="progress-stats mt-12">
          <div className="stat">
            <div className="stat-k">进步最快维度</div>
            <div className="stat-v">
              {progress.fastest_dimension || '—'}
              {Number(progress.fastest_delta) > 0 && (
                <span className="chip chip--ok mono" style={{ marginLeft: 8 }}>
                  +{fmt(progress.fastest_delta)}
                </span>
              )}
            </div>
          </div>
          <div className="stat">
            <div className="stat-k">仍需加强维度</div>
            <div className="stat-v">{progress.still_weak_dimension || '—'}</div>
          </div>
        </div>
        {progress.summary && <div className="alert alert--ok mt-12">{progress.summary}</div>}
      </SectionCard>

      {/* ================= 底部操作 ================= */}
      <section className="card mt-16 report-actions">
        <div className="feedback-box">
          <span className="field-label">评分是否准确？</span>
          <div className="row gap-8">
            <button
              className={`btn btn--sm ${feedback === true ? 'btn--ok' : 'btn--ghost'}`}
              onClick={() => onFeedback(true)}
            >
              <IconCheck size={16} /> 准确
            </button>
            <button
              className={`btn btn--sm ${feedback === false ? 'btn--danger' : 'btn--ghost'}`}
              onClick={() => onFeedback(false)}
            >
              <IconMinus size={16} /> 不太准
            </button>
            {feedback !== null && <span className="mute-2" style={{ fontSize: 12 }}>已记录，谢谢反馈</span>}
          </div>
        </div>
        <div className="action-buttons">
          <button className="btn btn--ghost" onClick={onExport} disabled={exporting}>
            {exporting ? <span className="spinner spinner--primary" /> : <IconDownload size={16} />} 导出 Markdown
          </button>
          <button className="btn btn--ghost" onClick={onPoster} disabled={posterLoading}>
            {posterLoading ? <span className="spinner spinner--primary" /> : <IconShare size={16} />} 生成分享海报
          </button>
          <button className="btn btn--primary" onClick={() => nav('/setup')}>
            <IconRefresh size={16} /> 再练一次
          </button>
        </div>
        <div className="row gap-12 mt-12" style={{ justifyContent: 'center', flexWrap: 'wrap' }}>
          <button className="link-btn" onClick={() => nav('/my-interviews?tab=wrongbook')}>
            去错题本看看
          </button>
          <button className="link-btn" onClick={() => nav('/leaderboard')}>
            查看排行榜
          </button>
        </div>
      </section>

      {/* 海报弹窗 */}
      {posterOpen && (
        <div className="modal-mask" role="dialog" aria-modal="true">
          <div className="modal">
            <div className="modal-head">
              <div>
                <div className="card-title">面试成绩单海报</div>
                <div className="sec-desc">750 × 1334，可直接分享到朋友圈</div>
              </div>
              <button className="btn btn--ghost btn--sm" onClick={() => setPosterOpen(false)}>
                关闭
              </button>
            </div>
            <div className="modal-body">
              {posterUrl ? (
                <img className="poster-img" src={posterUrl} alt="面试成绩单海报" />
              ) : (
                <div className="skeleton" style={{ height: 320 }} />
              )}
            </div>
            <div className="modal-foot">
              <div className="row row--between">
                <button className="btn btn--ghost" onClick={() => setPosterOpen(false)}>
                  取消
                </button>
                <button
                  className="btn btn--primary"
                  onClick={() => {
                    const a = document.createElement('a')
                    a.href = posterUrl
                    a.download = `面试成绩单_${ov.score || 0}分.png`
                    document.body.appendChild(a)
                    a.click()
                    document.body.removeChild(a)
                  }}
                >
                  <IconDownload size={16} /> 下载 PNG
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* 隐藏的画布，用于生成海报 */}
      <canvas ref={canvasRef} style={{ display: 'none' }} />
    </div>
  )
}
