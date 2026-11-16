/**
 * 答题录音的**本机**存储（IndexedDB）
 *
 * 为什么存本机而不是上传服务端：项目有一条隐私铁律——「音频不落盘、不出本机」。
 * 录音回放要能听，就得有持久化；把它放在浏览器本地是最小代价的解法：
 *  · 服务端一个字节都收不到，`data/uploads/` 里永远只有用户主动上传的简历/JD；
 *  · 用户清空浏览器数据即可彻底删除，不需要"申请删除"这种流程；
 *  · 代价（如实告知用户）：换浏览器/换设备/清缓存就没了，且只对**当前登录账号**可见。
 *
 * 数据结构：store = 'clips'，主键 `${userId}:${sessionId}:${index}`
 *   { key, userId, sessionId, index, section, seconds, size, createdAt, blob }
 *
 * 容量：`MAX_TOTAL_BYTES` 以内，超出按 createdAt 从旧到新淘汰（先删最老的场次）。
 * 所有 API 都在 IndexedDB 不可用时安全退化（返回空/抛可捕获的错误），
 * 绝不因为"存不下"影响面试主流程。
 */

const DB_NAME = 'aic_recordings'
const DB_VERSION = 1
const STORE = 'clips'

/** 总容量上限：约 60 分钟 opus 音频；超出即淘汰最旧的录音 */
export const MAX_TOTAL_BYTES = 40 * 1024 * 1024

export function isSupported() {
  try {
    return typeof window !== 'undefined' && !!window.indexedDB
  } catch {
    return false
  }
}

function openDb() {
  return new Promise((resolve, reject) => {
    if (!isSupported()) {
      reject(new Error('当前浏览器不支持本机录音存储（IndexedDB 不可用）'))
      return
    }
    const req = window.indexedDB.open(DB_NAME, DB_VERSION)
    req.onupgradeneeded = () => {
      const db = req.result
      if (!db.objectStoreNames.contains(STORE)) {
        const store = db.createObjectStore(STORE, { keyPath: 'key' })
        store.createIndex('userId', 'userId', { unique: false })
      }
    }
    req.onsuccess = () => resolve(req.result)
    req.onerror = () => reject(req.error || new Error('打开本机录音库失败'))
  })
}

function tx(db, mode, fn) {
  return new Promise((resolve, reject) => {
    const t = db.transaction(STORE, mode)
    const store = t.objectStore(STORE)
    let out
    try {
      out = fn(store)
    } catch (err) {
      reject(err)
      return
    }
    t.oncomplete = () => resolve(out?.result !== undefined ? out.result : out)
    t.onerror = () => reject(t.error || new Error('本机录音库操作失败'))
    t.onabort = () => reject(t.error || new Error('本机录音库操作被中止'))
  })
}

const clipKey = (userId, sessionId, index) => `${userId}:${sessionId}:${index}`

/** 列出某个账号的全部录音（按时间倒序），不含 blob 之外的计算字段 */
export async function listClips(userId) {
  if (!userId || !isSupported()) return []
  const db = await openDb()
  try {
    const all = await new Promise((resolve, reject) => {
      const t = db.transaction(STORE, 'readonly')
      const idx = t.objectStore(STORE).index('userId')
      const req = idx.getAll(userId)
      req.onsuccess = () => resolve(req.result || [])
      req.onerror = () => reject(req.error)
    })
    return all.sort((a, b) => (b.createdAt || 0) - (a.createdAt || 0))
  } finally {
    db.close()
  }
}

/** 按会话分组（最近在前），每组带总时长与总大小 */
export async function listSessions(userId) {
  const clips = await listClips(userId)
  const map = new Map()
  for (const c of clips) {
    if (!map.has(c.sessionId)) {
      map.set(c.sessionId, {
        sessionId: c.sessionId,
        createdAt: c.createdAt || 0,
        seconds: 0,
        size: 0,
        clips: [],
      })
    }
    const g = map.get(c.sessionId)
    g.clips.push(c)
    g.seconds += Number(c.seconds) || 0
    g.size += Number(c.size) || 0
    g.createdAt = Math.max(g.createdAt, c.createdAt || 0)
  }
  const groups = [...map.values()]
  // 组内按题号升序（回放顺序＝答题顺序），组间按最近时间倒序
  groups.forEach((g) => g.clips.sort((a, b) => (a.index || 0) - (b.index || 0)))
  return groups.sort((a, b) => b.createdAt - a.createdAt)
}

/** 保存一段录音；成功后做一次容量淘汰。返回是否真正写入。 */
export async function saveClip({ userId, sessionId, index, section = '', seconds = 0, blob }) {
  if (!userId || !sessionId || !blob || !isSupported()) return false
  const db = await openDb()
  try {
    const record = {
      key: clipKey(userId, sessionId, index),
      userId,
      sessionId,
      index: Number(index) || 0,
      section: String(section || ''),
      seconds: Math.max(0, Math.round(Number(seconds) || 0)),
      size: blob.size || 0,
      createdAt: Date.now(),
      blob,
    }
    await tx(db, 'readwrite', (store) => store.put(record))
  } finally {
    db.close()
  }
  await enforceStorageLimit(userId)
  return true
}

/** 删除一场面试的全部录音 */
export async function deleteSession(userId, sessionId) {
  if (!userId || !sessionId || !isSupported()) return 0
  const db = await openDb()
  try {
    const keys = await new Promise((resolve, reject) => {
      const t = db.transaction(STORE, 'readonly')
      const req = t.objectStore(STORE).index('userId').getAllKeys(userId)
      req.onsuccess = () => resolve(req.result || [])
      req.onerror = () => reject(req.error)
    })
    const mine = keys.filter((k) => String(k).startsWith(`${userId}:${sessionId}:`))
    await tx(db, 'readwrite', (store) => {
      mine.forEach((k) => store.delete(k))
      return mine.length
    })
    return mine.length
  } finally {
    db.close()
  }
}

/** 删除某一段录音 */
export async function deleteClip(userId, sessionId, index) {
  if (!userId || !isSupported()) return false
  const db = await openDb()
  try {
    await tx(db, 'readwrite', (store) => store.delete(clipKey(userId, sessionId, index)))
    return true
  } finally {
    db.close()
  }
}

/** 清空当前账号的全部录音 */
export async function clearUser(userId) {
  if (!userId || !isSupported()) return 0
  const db = await openDb()
  try {
    const keys = await new Promise((resolve, reject) => {
      const t = db.transaction(STORE, 'readonly')
      const req = t.objectStore(STORE).index('userId').getAllKeys(userId)
      req.onsuccess = () => resolve(req.result || [])
      req.onerror = () => reject(req.error)
    })
    await tx(db, 'readwrite', (store) => {
      keys.forEach((k) => store.delete(k))
      return keys.length
    })
    return keys.length
  } finally {
    db.close()
  }
}

/** 统计当前账号占用（字节 / 段数 / 场次） */
export async function usage(userId) {
  const clips = await listClips(userId)
  return {
    bytes: clips.reduce((n, c) => n + (Number(c.size) || 0), 0),
    clips: clips.length,
    sessions: new Set(clips.map((c) => c.sessionId)).size,
  }
}

/**
 * 超出本机存储上限时按时间从旧到新淘汰（一次会话一次会话地删，避免留下半场）。
 *
 * 这里的「上限」是**浏览器存储容量**，与账号的计费/次数无关。
 */
export async function enforceStorageLimit(userId) {
  const clips = await listClips(userId)
  let total = clips.reduce((n, c) => n + (Number(c.size) || 0), 0)
  if (total <= MAX_TOTAL_BYTES) return 0
  const bySession = new Map()
  for (const c of clips) {
    const g = bySession.get(c.sessionId) || { size: 0, at: c.createdAt || 0 }
    g.size += Number(c.size) || 0
    g.at = Math.min(g.at || c.createdAt || 0, c.createdAt || 0)
    bySession.set(c.sessionId, g)
  }
  const oldestFirst = [...bySession.entries()].sort((a, b) => a[1].at - b[1].at)
  let removed = 0
  for (const [sessionId, g] of oldestFirst) {
    if (total <= MAX_TOTAL_BYTES) break
    removed += await deleteSession(userId, sessionId)
    total -= g.size
  }
  return removed
}

/** 生成可播放的临时 URL。调用方负责在卸载时 revokeObjectUrl 释放。 */
export function blobUrl(blob) {
  try {
    return URL.createObjectURL(blob)
  } catch {
    return ''
  }
}

export function revokeUrl(url) {
  if (!url) return
  try {
    URL.revokeObjectURL(url)
  } catch {
    /* 忽略 */
  }
}

export function formatBytes(bytes) {
  const n = Number(bytes) || 0
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(0)} KB`
  return `${(n / 1024 / 1024).toFixed(1)} MB`
}

export function formatClock(seconds) {
  const s = Math.max(0, Math.round(Number(seconds) || 0))
  return `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`
}
