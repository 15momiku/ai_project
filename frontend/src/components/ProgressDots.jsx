import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { IconClock, IconWarn } from './icons'
import '../styles/progress.css'

/**
 * 面试顶部进度条（sticky）
 *
 * 设计要点（体验增强 §4 / §7）：
 *  - 进度必须是**点阵**，不是百分比：一题一个点，用户能数出「还剩几题」。
 *    已完成 = 实心主色；当前 = 描边 + 轻微呼吸；未到 = 弱化色。
 *    百分比只说「走了多远」，答不出「还剩几题」，而后者才是面试中真正想知道的。
 *  - 剩余时间只给**区间**（例如「预计还需 4–6 分钟」）。单点数字（“还需 5 分钟”）
 *    在面试里是假精度：一场面试的节奏本来就浮动，给区间才不会被数字追着跑。
 *  - 「退出」是破坏性操作：必须二次确认，且确认区里把默认/安全项做成「继续面试」。
 *  - 会话不可继续时绝不显示成「丢了」：明确说保留 + 可稍后继续 + 报告仍可生成。
 *
 * props
 *  - index:              当前第几题（从 1 开始；0 = 还没出题）
 *  - total:              总题数（0 = 题目还没定，条照常渲染，不炸）
 *  - answered:           已处理题数（跳过也算已处理）
 *  - stage:              当前环节名，可为空
 *  - elapsedSeconds:     已用秒数
 *  - estimatedRemaining: [下限, 上限] 秒；可能为 [] 或 [0,0]（此时不显示）
 *  - source:             'session' | 'history' | 'estimate'（估算口径）
 *  - canResume:          false = 会话已不可继续作答（显示安抚文案）
 *  - idleTimeoutHours:   闲置回收时长（小时），用于安抚文案
 *  - onExit:             用户**确认退出**后才调用的回调
 */

/** 超过这个题数就把点阵改成「可横向滑动」，而不是硬挤成一条（默认 9 题时不触发） */
const SCROLL_FROM = 12
/** 一次最多渲染的点数：60 题的极端场景下也不生成 60 个节点 */
const MAX_DOTS = 40
/** 剩余时长文案里不再展开区间的下限：低于 1 分钟说「不到 1 分钟」更诚实 */
const SOURCE_HINT = {
  history: '（按你的历史节奏）',
  session: '（按本场节奏推算）',
  estimate: '（按 45 秒/题估算）',
}

/** 秒 → mm:ss */
function clock(totalSeconds) {
  const s = Math.max(0, Math.floor(Number(totalSeconds) || 0))
  const mm = String(Math.floor(s / 60)).padStart(2, '0')
  const ss = String(s % 60).padStart(2, '0')
  return `${mm}:${ss}`
}

/** 秒 → 「不到 1 分钟 / N 分钟 / N–M 分钟」；算不上来就返回空串（宁可不显示，也不显示 0 分钟） */
function minuteRange(remaining) {
  const list = Array.isArray(remaining) ? remaining : []
  const lo = Math.max(0, Math.round(Number(list[0]) || 0))
  const hi = Math.max(0, Math.round(Number(list[1]) || 0))
  // 空数组 / [0,0]：没有可信区间 → 整句不显示
  if (!lo && !hi) return ''
  if (hi <= 60) return '不到 1 分钟'
  const loMin = Math.max(1, Math.round(lo / 60))
  const hiMin = Math.max(loMin + (lo > 0 ? 0 : 1), Math.round(hi / 60))
  return loMin === hiMin ? `约 ${loMin} 分钟` : `${loMin}–${hiMin} 分钟`
}

export default function ProgressDots({
  index = 0,
  total = 0,
  answered = 0,
  stage = '',
  elapsedSeconds = 0,
  estimatedRemaining = [],
  source = 'estimate',
  canResume = true,
  idleTimeoutHours = 6,
  onExit,
  children,
}) {
  const [confirming, setConfirming] = useState(false)
  const cancelRef = useRef(null)
  const exitRef = useRef(null)

  const n = Math.max(0, Math.floor(Number(total) || 0))
  const hasQuestion = Number(index) > 0 && n > 0
  const cur = hasQuestion ? Math.min(Math.floor(Number(index)), n) : 0
  const done = Math.min(Math.max(0, Math.floor(Number(answered) || 0)), n)

  /* 确认弹层：Esc 关闭 + 焦点落到「继续面试」（安全项），避免误触确认退出 */
  useEffect(() => {
    if (!confirming) return undefined
    const onKey = (e) => {
      if (e.key === 'Escape') {
        setConfirming(false)
        // 焦点还给触发按钮，键盘用户知道自己在哪
        if (exitRef.current) exitRef.current.focus()
      }
    }
    window.addEventListener('keydown', onKey)
    if (cancelRef.current) cancelRef.current.focus()
    return () => window.removeEventListener('keydown', onKey)
  }, [confirming])

  const closeConfirm = () => {
    setConfirming(false)
    if (exitRef.current) exitRef.current.focus()
  }

  const doExit = () => {
    setConfirming(false)
    if (typeof onExit === 'function') onExit()
  }

  const rangeText = minuteRange(estimatedRemaining)
  const hint = SOURCE_HINT[source] || ''
  const hours = Math.max(1, Math.floor(Number(idleTimeoutHours) || 0) || 6)
  const scrolled = n > SCROLL_FROM
  const shownTotal = Math.min(n, MAX_DOTS)
  const truncating = shownTotal < n
  const dotsText = n
    ? `第 ${cur || 1} 题，共 ${n} 题，已完成 ${done} 题`
    : '面试题目尚未生成'
  // 点阵被截断时（>40 题）不在题号里写总数，避免「显示 40 个点却说 47 题」的对不上
  const stageLabel = hasQuestion ? (truncating ? `第 ${cur} 题` : `第 ${cur} / ${n} 题`) : '准备中'

  /* 点阵：一题一个点。completed = 主色实心；current = 描边 + 呼吸；其余 = 弱化色 */
  const dots = n
    ? Array.from({ length: shownTotal }, (_, i) => {
        const pos = i + 1
        const cls =
          pos === cur ? 'pd-dot pd-dot--now' : pos <= done ? 'pd-dot pd-dot--done' : 'pd-dot'
        return (
          <i
            key={pos}
            className={cls}
            /* 呼吸动画只是氛围：对读屏无用，隐藏掉，进度由容器 aria-label 用文字交代 */
            aria-hidden="true"
          />
        )
      })
    : null

  return (
    <div className="pd-wrap">
      <div className="iv-top pd-bar">
        {/* 顶部第一行留给页面自己的内容（AI 标识 / 岗位名 / 静音开关），
            这样整条顶栏只有一层 sticky 毛玻璃，不会出现两层玻璃叠加 */}
        {children ? <div className="pd-head">{children}</div> : null}

        {/* 进度点阵：可横向滑动，绝不换行、不撑破顶栏 */}
        <div
          className={`pd-dots${scrolled ? ' pd-dots--scroll' : ''}`}
          role="img"
          aria-label={dotsText}
        >
          {dots}
        </div>

        <div className="pd-row">
          <span className="pd-stage">
            {stageLabel}
            {stage ? <span className="pd-stage-name">{stage}</span> : null}
          </span>

          <span className="pd-spacer" />

          {rangeText ? (
            <span className="pd-left">
              <span className="pd-left-text">预计还需 {rangeText}</span>
              {hint ? <span className="pd-hint">{hint}</span> : null}
            </span>
          ) : null}

          {hasQuestion || done > 0 ? (
            <span className="pd-answered">已答 {done}</span>
          ) : null}

          <span className="pd-elapsed">
            <IconClock size={14} />
            <span className="pd-num">已用 {clock(elapsedSeconds)}</span>
          </span>

          <button
            type="button"
            ref={exitRef}
            className="pd-exit"
            aria-expanded={confirming}
            aria-haspopup="dialog"
            onClick={() => setConfirming(true)}
          >
            退出
          </button>
        </div>

        {!canResume ? (
          <p className="pd-notice" role="status">
            <IconWarn size={14} />
            <span>
              会话将保留，可稍后继续（{hours} 小时内回来还能接着答），报告仍可生成。
            </span>
          </p>
        ) : null}
      </div>

      {/* 二次确认：内嵌在组件里，只有点了「确认退出」才回调 onExit */}
      {confirming
        ? createPortal(
            <>
              <div className="pd-backdrop" onClick={closeConfirm} />
              <div className="pd-confirm" role="dialog" aria-modal="true" aria-label="确认退出面试">
                <div className="pd-confirm-title">退出当前面试？</div>
                <p className="pd-confirm-desc">
                  已答的 {done} 题会保留，{hours} 小时内回来还能接着答，报告也可以照常生成。
                </p>
                <div className="pd-confirm-acts">
                  <button type="button" ref={cancelRef} className="btn btn--ghost" onClick={closeConfirm}>
                    继续面试
                  </button>
                  <button type="button" className="btn btn--danger" onClick={doExit}>
                    确认退出
                  </button>
                </div>
              </div>
            </>,
            document.body
          )
        : null}
    </div>
  )
}
