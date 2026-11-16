import { useCallback, useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import SectionCard from '../components/SectionCard'
import { deleteWrong, getWrongQuestions, notify, retryWrong } from '../api/client'
import {
  IconBook,
  IconBulb,
  IconDoc,
  IconMic,
  IconReplay,
  IconTarget,
  IconTrash,
  IconWarn,
} from '../components/icons'
import { dimClass } from '../constants/dimensions'
import './wrongbook.css'

const CATEGORIES = ['专业类', '逻辑类', '表达类', '应变类', '匹配类', '文化类']

/**
 * 错题本页面。
 *
 * 内容抽成 `WrongBookPanel` 以便复用：「我的面试 · 错题本」标签页直接渲染它，
 * 避免把同一套筛选/重练逻辑写两遍（那必然会改一处忘一处）。
 * `embedded` 为 true 时不渲染外层 container 与页头——外层由标签页提供。
 */
export default function WrongBook() {
  return <WrongBookPanel />
}

export function WrongBookPanel({ embedded = false }) {
  const nav = useNavigate()
  const [data, setData] = useState(null)
  const [items, setItems] = useState([])
  const [active, setActive] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [retryData, setRetryData] = useState(null)
  const [retryOpen, setRetryOpen] = useState(false)
  const [busyId, setBusyId] = useState('')

  const load = useCallback(
    async (category = '') => {
      setLoading(true)
      setError('')
      try {
        const res = await getWrongQuestions(category)
        setData(res)
        setItems(res?.items || [])
      } catch (err) {
        setError(err?.message || '错题本加载失败')
        setItems([])
      } finally {
        setLoading(false)
      }
    },
    []
  )

  useEffect(() => {
    load(active)
  }, [active, load])

  const onRetry = async (item) => {
    setBusyId(item.id)
    try {
      const res = await retryWrong(item.id)
      setRetryData({ ...res, item })
      setRetryOpen(true)
      // 重做次数会递增，刷新列表里的计数
      setItems((prev) =>
        prev.map((it) => (it.id === item.id ? { ...it, retry_count: res?.retry_count ?? it.retry_count } : it))
      )
    } catch {
      /* 已弹 Toast */
    } finally {
      setBusyId('')
    }
  }

  /**
   * 单题特训（体验增强 §9）：不再跑整场 9 题，而是立刻开一场
   * 同环节的 3 题微面试，并带着该维度的历史得分作为前后对比基准。
   */
  const onMicro = async (item) => {
    setBusyId(item.id)
    try {
      const res = await retryWrong(item.id, true)
      const micro = res?.micro
      if (!micro?.session_id) {
        notify('微面试暂时创建不了（可能源会话的简历/JD 已删除），先用参考思路练一遍', 'warn', 4000)
        setRetryData({ ...res, item })
        setRetryOpen(true)
        return
      }
      notify(
        `已开始「${micro.focus_section || '薄弱环节'}」3 题特训，约 4 分钟`,
        'success',
        3200,
      )
      // 不做 state 传递：面试页在没有 start state 时会从
      // GET /api/interview/{id} 恢复并朗读当前题，效果一致且少一处字段耦合。
      nav(`/interview/${micro.session_id}`)
    } catch {
      /* 已弹 Toast */
    } finally {
      setBusyId('')
    }
  }

  const onDelete = async (item) => {
    if (!window.confirm('确定从错题本中删除这道题吗？（不影响已有报告）')) return
    setBusyId(item.id)
    try {
      const res = await deleteWrong(item.id)
      // 后端删除失败时返回的是 HTTP 200 + {ok:false}，只判 HTTP 状态会把失败当成功，
      // 界面显示「已移除」但刷新后条目又回来了。
      if (res && res.ok === false) {
        notify(res.message || '删除失败，请稍后重试', 'error', 3200)
        return
      }
      setItems((prev) => prev.filter((it) => it.id !== item.id))
      notify('已从错题本移除', 'success')
      // 重新拉取列表：让 total / by_category 计数与后端保持一致
      load(active)
    } catch {
      /* 已弹 Toast */
    } finally {
      setBusyId('')
    }
  }

  const byCategory = data?.by_category || {}

  return (
    <div className={embedded ? '' : 'container'}>
      <div className={embedded ? 'wb-head-embedded' : 'page-head'}>
        {!embedded && (
          <div>
            <h1 className="page-title">错题本</h1>
            <p className="page-sub">单题得分低于 6 分的题会自动收进这里，针对性重练效果最好</p>
          </div>
        )}
        {embedded && (
          <p className="page-sub">
            单题得分低于 6 分的题会自动收进这里；点「重做」进入同薄弱环节的 3 题微面试。
          </p>
        )}
        <button className="btn btn--ghost btn--sm" onClick={() => load(active)}>
          刷新
        </button>
      </div>

      {/* 分类筛选 */}
      <div className="wb-tabs">
        <button className={`wb-tab ${active === '' ? 'on' : ''}`} onClick={() => setActive('')}>
          全部
          <span className="wb-tab-count mono">{data?.total ?? 0}</span>
        </button>
        {CATEGORIES.map((c) => (
          <button key={c} className={`wb-tab ${dimClass(c)} ${active === c ? 'on' : ''}`} onClick={() => setActive(c)}>
            <i className="dim-dot" />
            {c}
            <span className="wb-tab-count mono">{byCategory[c] ?? 0}</span>
          </button>
        ))}
      </div>

      {loading ? (
        <div className="col gap-12 mt-16">
          {Array.from({ length: 3 }).map((_, i) => (
            <div className="card" key={i}>
              <div className="skeleton sk-line sk-line--40" />
              <div className="skeleton sk-line" />
              <div className="skeleton sk-line sk-line--60" />
            </div>
          ))}
        </div>
      ) : error ? (
        <div className="card mt-16">
          <div className="empty">
            <IconWarn size={26} style={{ color: 'var(--text-mute)' }} />
            <div className="empty-title">错题本加载失败</div>
            <div className="empty-desc">{error}</div>
            <button className="btn btn--primary mt-16" onClick={() => load(active)}>
              重试
            </button>
          </div>
        </div>
      ) : items.length === 0 ? (
        <div className="card mt-16">
          <div className="empty">
            <IconBook size={30} style={{ color: 'var(--text-mute)' }} />
            <div className="empty-title">
              {active ? `「${active}」还没有错题` : '错题本还是空的'}
            </div>
            <div className="empty-desc">
              完成一次模拟面试后，得分低于 6 分的题目会自动收进错题本；
              <br />
              你也可以先去看看排行榜，了解同岗位同学的分数分布。
            </div>
            <div className="row gap-12 mt-16" style={{ justifyContent: 'center', flexWrap: 'wrap' }}>
              <button className="btn btn--primary" onClick={() => nav('/setup')}>
                去做一次模拟面试
              </button>
              <button className="btn btn--ghost" onClick={() => nav('/leaderboard')}>
                查看排行榜
              </button>
            </div>
          </div>
        </div>
      ) : (
        <div className="wb-list mt-16">
          {items.map((item) => (
            <SectionCard key={item.id} className="wb-card">
              <div className="wb-head">
                <div className="row gap-8 row--wrap">
                  {item.category && (
                    <span className={`chip chip--dim ${dimClass(item.category)}`}>{item.category}</span>
                  )}
                  {item.focus && <span className="chip">考察：{item.focus}</span>}
                  {item.job_title && <span className="chip chip--accent">{item.job_title}</span>}
                </div>
                <div className="row gap-8">
                  <span className="wb-score mono">{Number(item.score || 0).toFixed(1)} 分</span>
                  {item.retry_count > 0 && <span className="chip chip--ok">已重练 {item.retry_count} 次</span>}
                </div>
              </div>

              <div className="wb-question">{item.question || '（题目缺失）'}</div>

              {/* 体验增强 §9：把「练一道题」的单位从整场变成 3 题微面试 */}
              <div className="wb-train">
                <div className="grow">
                  <span className="wb-train-title">
                    薄弱项：{item.section || '综合'} · {item.category || '待分类'}
                  </span>
                  <span className="mute-2 wb-train-sub">
                    {item.issue ? `你的失分主要在「${item.issue}」` : '针对这道题的同一环节再练 3 题'}
                    {Number(item.before_score) > 0
                      ? `　上次该维度 ${Number(item.before_score).toFixed(1)} 分`
                      : ''}
                  </span>
                </div>
                <button
                  className="btn btn--primary btn--sm"
                  onClick={() => onMicro(item)}
                  disabled={busyId === item.id}
                >
                  {busyId === item.id ? <span className="spinner" /> : <IconTarget size={16} />} 练 3 题 · 约 4 分钟
                </button>
                <button className="btn btn--ghost btn--sm" onClick={() => onRetry(item)} disabled={busyId === item.id}>
                  <IconDoc size={16} /> 只看参考答案
                </button>
              </div>

              <div className="wb-block">
                <div className="wb-block-k">你的回答</div>
                <p className="wb-block-v">{item.answer || '（未作答）'}</p>
              </div>
              {item.issue && (
                <div className="wb-block">
                  <div className="wb-block-k danger">问题所在</div>
                  <p className="wb-block-v">{item.issue}</p>
                </div>
              )}
              {item.correct_thinking && (
                <div className="wb-block">
                  <div className="wb-block-k ok">正确思路</div>
                  <p className="wb-block-v">{item.correct_thinking}</p>
                </div>
              )}

              <div className="wb-actions">
                <button className="btn btn--ghost btn--sm" onClick={() => onRetry(item)} disabled={busyId === item.id}>
                  <IconReplay size={16} /> 参考思路
                </button>
                <button className="btn btn--danger btn--sm" onClick={() => onDelete(item)} disabled={busyId === item.id}>
                  <IconTrash size={16} /> 删除
                </button>
                <span className="mute-2" style={{ fontSize: 11.5 }}>
                  {item.created_at ? `收录于 ${String(item.created_at).slice(0, 10)}` : ''}
                </span>
              </div>
            </SectionCard>
          ))}
        </div>
      )}

      {/* 重练弹窗 */}
      {retryOpen && retryData && (
        <div className="modal-mask" role="dialog" aria-modal="true">
          <div className="modal">
            <div className="modal-head">
              <div>
                <div className="card-title">针对错题重练</div>
                <div className="sec-desc">已重练 {retryData.retry_count || 1} 次 · 按下面的思路再答一遍</div>
              </div>
              <button className="btn btn--ghost btn--sm" onClick={() => setRetryOpen(false)}>
                关闭
              </button>
            </div>
            <div className="modal-body">
              <div className="wb-block">
                <div className="wb-block-k">原题</div>
                <p className="wb-block-v">{retryData.question || retryData.item?.question}</p>
              </div>
              <div className="block-label"><IconBulb size={15} /> 建议思路</div>
              <ol className="wb-tips">
                {(retryData.tips || []).map((t, i) => (
                  <li key={i}>
                    <span className="imp-step mono">{i + 1}</span>
                    <span>{t}</span>
                  </li>
                ))}
              </ol>
              {retryData.reference && (
                <>
                  <div className="block-label">
                    <IconDoc size={15} /> 参考答案要点
                  </div>
                  <p className="wb-ref">{retryData.reference}</p>
                </>
              )}
            </div>
            <div className="modal-foot">
              <div className="row row--between">
                <button className="btn btn--ghost" onClick={() => setRetryOpen(false)}>
                  稍后再说
                </button>
                <button
                  className="btn btn--primary"
                  onClick={() => {
                    setRetryOpen(false)
                    nav('/setup')
                  }}
                >
                  <IconMic size={16} /> 去练习同类题
                </button>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
