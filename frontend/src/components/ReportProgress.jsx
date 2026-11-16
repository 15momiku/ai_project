import { useEffect, useRef, useState } from 'react'
import { getReportProgress } from '../api/client'
import { IconCheck, IconInfo } from './icons'
import '../styles/report-progress.css'

/**
 * 报告生成进度（生成过程可视化）
 *
 * 为什么需要：一份报告要跑两次大模型评分（首轮 + 二次校验），实测数十秒；此前页面只有
 * 一个骨架屏和「请稍候」，用户无法判断是在正常处理还是卡死，于是刷新页面或反复点重新生成。
 *
 * 诚实原则（贯穿本项目）：
 *  - 百分比只取后端**已真正完成**的阶段数，不为了让进度条好看而虚增；
 *  - 当前阶段用「动效 + 已耗时」表达"在动"，而不是把数字往上推；
 *  - 拿不到进度（多进程部署、尚未开始）时显示**不确定进度条**并说明原因，不假装有进度；
 *  - 后端报告 `stale`（超出预期时长仍在跑）时明确提示，并给出「刷新」的出口。
 *
 * props
 *  - sessionId: 会话 id
 *  - active:    是否正在生成（false 时停止轮询）
 *  - variant:   'full'（整页，无历史报告时）| 'compact'（页头横幅，重新生成时保留旧报告）
 *  - onSettled: 进度结束（done/failed）时回调，供调用方收尾
 */
const POLL_MS = 700
// 生成很快（规则引擎通常 <1s）时不闪进度条：短暂出现又消失比骨架屏更晃眼
const REVEAL_DELAY_MS = 600

const SUBTITLE = {
  load: '正在读取本场对话与简历/JD',
  score: '正在评分，这一段最慢',
  enrich: '正在计算维度依据与同岗位基准',
  plan: '正在生成改进建议与 7 天计划',
  questions: '正在整理逐题分析与原文证据',
  persist: '正在保存报告',
}

export default function ReportProgress({ sessionId, active = true, variant = 'full', onSettled }) {
  const [snap, setSnap] = useState(null)
  const [visible, setVisible] = useState(false)
  const settledRef = useRef('')

  useEffect(() => {
    if (!active || !sessionId) return undefined
    let stop = false
    const timer = setTimeout(() => !stop && setVisible(true), REVEAL_DELAY_MS)

    const tick = async () => {
      try {
        const data = await getReportProgress(sessionId)
        if (stop) return
        setSnap(data)
        const status = data?.status || ''
        if (status === 'done' || status === 'failed') {
          // 只回调一次：轮询可能在结束态多跑一两拍
          if (settledRef.current !== status) {
            settledRef.current = status
            onSettled?.(data)
          }
        }
      } catch {
        // 拿不到进度不改页面：继续按不确定进度展示（后端可能重启/多进程）
      }
    }

    tick()
    const id = setInterval(tick, POLL_MS)
    return () => {
      stop = true
      clearTimeout(timer)
      clearInterval(id)
    }
  }, [sessionId, active, onSettled])

  useEffect(() => {
    if (active) return
    // 生成结束：收起进度（父组件通常已经切到报告内容）
    setVisible(false)
    settledRef.current = ''
  }, [active])

  const percent = Math.max(0, Math.min(100, Number(snap?.percent) || 0))
  const available = Boolean(snap?.available)
  const stages = snap?.stages || []
  const elapsed = Number(snap?.elapsed_seconds) || 0
  const mode = snap?.scoring_mode || ''
  const stale = Boolean(snap?.stale)

  const modeHint = (() => {
    if (mode === 'rule') return '当前用规则引擎评分，通常几秒内完成'
    if (mode === 'llm' || mode === 'hybrid') return '当前用大模型评分，整份报告通常需要数十秒'
    return '大模型评分通常需要数十秒，规则引擎兜底只需几秒'
  })()

  const failed = snap?.status === 'failed'

  // 读屏播报：只报「阶段变化」，绝不把每秒跳动的时间塞进 live region
  // （那是把 aria-live 用在滚动数字上，屏幕阅读器会被刷屏）。
  // 这个字符串只在阶段/百分比变化时才变，因此一句一播报。
  const liveText = failed
    ? '报告生成遇到问题'
    : available
      ? `正在${snap?.stage_label || '生成报告'}，已完成 ${snap?.stage_index ? Number(snap.stage_index) - 1 : 0}/${stages.length} 个阶段`
      : '正在生成报告，本次无法获取阶段进度'

  return (
    <section className={`rp rp--${variant} ${visible ? '' : 'rp--hidden'}`} aria-label="报告生成进度">
      <p className="sr-only" role="status" aria-live="polite" aria-atomic="true">
        {liveText}
      </p>
      <div className="rp-head">
        <span className="rp-title">
          {failed ? '报告生成遇到问题' : snap?.stage_label ? `正在${snap.stage_label}` : '正在生成六维评分报告'}
        </span>
        <span className="rp-elapsed mono">{elapsed > 0 ? `已用 ${formatSeconds(elapsed)}` : ''}</span>
      </div>

      {/* 进度条：拿不到真实进度时用不确定动效，绝不显示假百分比 */}
      <div
        className={`rp-track ${available ? '' : 'rp-track--indeterminate'}`}
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={available ? percent : undefined}
        aria-valuetext={available ? `已完成 ${percent}%，共 ${stages.length} 个阶段` : '进度未知'}
      >
        {available ? (
          <div className={`rp-fill ${snap?.status === 'running' ? 'rp-fill--active' : ''}`} style={{ width: `${percent}%` }} />
        ) : (
          <div className="rp-fill rp-fill--ghost" />
        )}
      </div>

      <div className="rp-meta">
        {available ? (
          <span className="mono">
            {snap?.stage_index || 0}/{snap?.total_stages || stages.length} 阶段 · {percent}%
          </span>
        ) : (
          <span>进度未知</span>
        )}
        <span className="mute-2">{modeHint}</span>
      </div>

      {variant === 'full' && stages.length > 0 && (
        <ol className="rp-steps">
          {stages.map((s) => (
            <li key={s.key} className={`rp-step rp-step--${s.status}`}>
              <span className="rp-dot" aria-hidden="true">
                {s.status === 'done' ? <IconCheck size={12} /> : null}
              </span>
              <span className="rp-step-label">{s.label}</span>
              {s.detail && s.status === 'active' && <span className="rp-step-detail">{s.detail}</span>}
              {s.status === 'done' && s.seconds != null && (
                <span className="rp-step-sec mono mute-2">{formatSeconds(s.seconds)}</span>
              )}
              {s.status === 'active' && s.seconds != null && (
                <span className="rp-step-sec mono">{formatSeconds(s.seconds)}</span>
              )}
              <span className="sr-only">
                {s.status === 'done' ? '已完成' : s.status === 'active' ? '进行中' : '未开始'}
              </span>
            </li>
          ))}
        </ol>
      )}

      {variant === 'full' && available && !failed && (
        <p className="rp-hint mute-2">
          {stages.find((s) => s.status === 'active')?.detail ||
            SUBTITLE[snap?.stage] ||
            '这一步由后端完成，可以先去忙别的，页面会自动刷新出报告。'}
        </p>
      )}

      {failed && (
        <p className="rp-hint rp-hint--warn">
          <IconInfo size={14} /> {snap?.message || '生成过程中断，下面可以重试。'}
        </p>
      )}

      {!available && !failed && (
        <p className="rp-hint mute-2">
          <IconInfo size={14} /> {snap?.note || '这次拿不到阶段进度，页面会在报告生成好后自动显示。'}
        </p>
      )}

      {stale && (
        <p className="rp-hint rp-hint--warn">
          <IconInfo size={14} /> 耗时明显长于预期：大模型可能正在排队。若长时间没有结果，可刷新页面重试。
        </p>
      )}
    </section>
  )
}

function formatSeconds(value) {
  const s = Math.max(0, Number(value) || 0)
  if (s < 60) return `${s.toFixed(s < 10 ? 1 : 0)}s`
  const m = Math.floor(s / 60)
  return `${m}m${String(Math.round(s % 60)).padStart(2, '0')}s`
}
