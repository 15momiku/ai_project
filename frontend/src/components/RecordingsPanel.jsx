import { useCallback, useEffect, useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { IconClock, IconMic, IconPlay, IconTrash, IconVolume } from './icons'
import {
  blobUrl,
  clearUser,
  deleteClip,
  deleteSession,
  formatBytes,
  formatClock,
  isSupported,
  listSessions,
  revokeUrl,
  usage,
} from '../storage/recordings'

/**
 * 录音回放（「我的面试 · 录音回放」）
 *
 * 数据来源是**浏览器本机**（IndexedDB），不上传服务端 —— 项目的隐私铁律是
 * 「音频不落盘、不出本机」。因此这里必须如实告诉用户三件事：
 *   1. 录音只在这台设备的这个浏览器里；
 *   2. 清浏览器数据 / 换设备就没了；
 *   3. 只有登录后作答的语音题才会录（游客不保存任何数据）。
 *
 * props
 *  - userId: 当前登录用户 id（按账号隔离，换账号看不到别人的录音）
 */
/** 只展示最近 N 场，与「面试报告」标签页的口径保持一致 */
const RECENT_SESSIONS = 10

export default function RecordingsPanel({ userId }) {
  const nav = useNavigate()
  const [groups, setGroups] = useState([])
  const [stat, setStat] = useState({ bytes: 0, clips: 0, sessions: 0 })
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [urls, setUrls] = useState({})
  const [pending, setPending] = useState('') // 正在二次确认的删除目标
  const supported = useMemo(() => isSupported(), [])

  const load = useCallback(async () => {
    if (!userId || !supported) {
      setLoading(false)
      return
    }
    setLoading(true)
    setError('')
    try {
      const list = await listSessions(userId)
      // 与「面试报告」保持一致：只展示最近 10 场（更早的由配额淘汰或留在库里不展示）
      const recent = list.slice(0, RECENT_SESSIONS)
      setGroups(recent)
      setStat(await usage(userId))
      // 一次性生成本次要用的 object URL，卸载/刷新时统一回收（避免内存泄漏）
      const next = {}
      for (const g of recent) {
        for (const c of g.clips) next[c.key] = blobUrl(c.blob)
      }
      setUrls((prev) => {
        Object.values(prev).forEach(revokeUrl)
        return next
      })
    } catch (err) {
      setError(err?.message || '读取本机录音失败')
      setGroups([])
    } finally {
      setLoading(false)
    }
  }, [userId, supported])

  useEffect(() => {
    load()
  }, [load])

  // 卸载时回收所有 object URL
  useEffect(
    () => () => {
      setUrls((prev) => {
        Object.values(prev).forEach(revokeUrl)
        return {}
      })
    },
    []
  )

  const onDeleteClip = async (clip) => {
    setPending('')
    try {
      await deleteClip(userId, clip.sessionId, clip.index)
      revokeUrl(urls[clip.key])
      await load()
    } catch {
      setError('删除失败，请重试')
    }
  }

  const onDeleteSession = async (g) => {
    setPending('')
    try {
      await deleteSession(userId, g.sessionId)
      await load()
    } catch {
      setError('删除失败，请重试')
    }
  }

  const onClearAll = async () => {
    setPending('')
    try {
      await clearUser(userId)
      await load()
    } catch {
      setError('清空失败，请重试')
    }
  }

  if (!supported) {
    return (
      <div className="card">
        <div className="empty">
          <IconVolume size={28} style={{ color: 'var(--text-mute)' }} />
          <div className="empty-title">当前浏览器不支持本机录音存储</div>
          <div className="empty-desc">
            录音需要浏览器的 IndexedDB 能力。换用 Chrome / Edge / Safari 的较新版本即可；
            面试与报告功能不受影响。
          </div>
        </div>
      </div>
    )
  }

  if (loading) {
    return (
      <div className="card">
        <div className="skeleton sk-line sk-line--40" />
        <div className="skeleton sk-line" style={{ marginTop: 10 }} />
        <div className="skeleton sk-line sk-line--60" style={{ marginTop: 10 }} />
      </div>
    )
  }

  if (!groups.length) {
    return (
      <div className="card">
        <div className="empty">
          <IconMic size={30} style={{ color: 'var(--text-mute)' }} />
          <div className="empty-title">还没有录音</div>
          <div className="empty-desc">
            语音模式作答时，你的回答会录一份存在<strong>这台设备</strong>上，方便事后回听；
            <br />
            文字作答不会产生录音，游客模式的作答不保存（因此也不会有录音）。
          </div>
          <button className="btn btn--primary mt-16" onClick={() => nav('/setup')}>
            <IconMic size={16} /> 去做一次语音面试
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="rec">
      <div className="rec-bar">
        <div className="rec-stat">
          最近 <span className="mono">{groups.length}</span> 场 ·{' '}
          <span className="mono">{stat.clips}</span> 段录音 ·{' '}
          <span className="mono">{formatBytes(stat.bytes)}</span>
          <span className="mute-2">（只存在本机浏览器，不会上传）</span>
        </div>
        <div className="row gap-8">
          <button className="btn btn--ghost btn--sm" onClick={load}>
            刷新
          </button>
          {pending === 'all' ? (
            <span className="row gap-8">
              <button className="btn btn--danger btn--sm" onClick={onClearAll}>
                确认清空
              </button>
              <button className="btn btn--ghost btn--sm" onClick={() => setPending('')}>
                取消
              </button>
            </span>
          ) : (
            <button className="btn btn--ghost btn--sm" onClick={() => setPending('all')}>
              <IconTrash size={15} /> 清空全部
            </button>
          )}
        </div>
      </div>

      {error && <div className="alert alert--danger mb-12">{error}</div>}

      {groups.map((g) => (
        <section className="rec-group" key={g.sessionId}>
          <div className="rec-group-head">
            <div className="grow">
              <span className="rec-group-date mono">{new Date(g.createdAt).toLocaleString('zh-CN')}</span>
              <span className="mute-2">
                {' '}
                · {g.clips.length} 题 · {formatClock(g.seconds)} · {formatBytes(g.size)}
              </span>
            </div>
            {pending === g.sessionId ? (
              <span className="row gap-8">
                <button className="btn btn--danger btn--sm" onClick={() => onDeleteSession(g)}>
                  确认删除本场
                </button>
                <button className="btn btn--ghost btn--sm" onClick={() => setPending('')}>
                  取消
                </button>
              </span>
            ) : (
              <button className="link-btn" onClick={() => setPending(g.sessionId)}>
                <IconTrash size={14} /> 删除本场
              </button>
            )}
          </div>

          <ul className="rec-list">
            {g.clips.map((c) => (
              <li className="rec-item" key={c.key}>
                <span className="rec-idx mono">第 {c.index || '?'} 题</span>
                {c.section && <span className="chip">{c.section}</span>}
                <span className="rec-sec mono mute-2">
                  <IconClock size={13} /> {formatClock(c.seconds)}
                </span>
                <audio className="rec-audio" controls preload="none" src={urls[c.key] || ''} />
                <button
                  className="link-btn rec-del"
                  onClick={() => (pending === c.key ? onDeleteClip(c) : setPending(c.key))}
                  aria-label={`删除第 ${c.index} 题的录音`}
                >
                  {pending === c.key ? '确认删除' : <IconTrash size={15} />}
                </button>
              </li>
            ))}
          </ul>
        </section>
      ))}

      <p className="mute-2 mt-12" style={{ fontSize: 12 }}>
        <IconPlay size={12} /> 录音保存在这台设备的浏览器里（IndexedDB），不会上传服务端；
        清理浏览器数据或更换设备后将无法找回。容量上限 40 MB，超出后自动淘汰最早的场次。
      </p>
    </div>
  )
}
