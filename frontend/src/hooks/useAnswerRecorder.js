import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * 答题录音（本机保存，用于「我的面试 · 录音回放」）
 *
 * 与 `useRecorder`（ASR）**职责分开**：那个负责把语音转成文字，识别完就把音频丢了；
 * 这个负责把这一题的原始音频留下来，供事后回放。两者并行工作、互不依赖：
 * ASR 走浏览器 Web Speech 或阿里云，本 Hook 只做 MediaRecorder 采集。
 *
 * 隐私：音频只在本机内存里停留到 stop()，随后交给 `storage/recordings` 存进
 * 浏览器 IndexedDB，**不上传服务端**（项目铁律：音频不落盘、不出本机）。
 *
 * 只在**登录模式**下启用：游客的数据不保存，录音同理（见 Interview 页的 wiring）。
 */
const MIME_CANDIDATES = [
  'audio/webm;codecs=opus',
  'audio/webm',
  'audio/ogg;codecs=opus',
  'audio/mp4',
]

function pickMime() {
  if (typeof window === 'undefined' || !window.MediaRecorder) return ''
  if (typeof MediaRecorder.isTypeSupported !== 'function') return ''
  for (const m of MIME_CANDIDATES) {
    try {
      if (MediaRecorder.isTypeSupported(m)) return m
    } catch {
      /* 忽略 */
    }
  }
  return ''
}

export default function useAnswerRecorder() {
  const supported =
    typeof window !== 'undefined' &&
    !!window.MediaRecorder &&
    !!navigator?.mediaDevices?.getUserMedia

  const [recording, setRecording] = useState(false)
  const [error, setError] = useState('')

  const mediaRef = useRef(null)
  const streamRef = useRef(null)
  const chunksRef = useRef([])
  const startedAtRef = useRef(0)

  const cleanup = useCallback(() => {
    try {
      streamRef.current?.getTracks()?.forEach((t) => t.stop())
    } catch {
      /* 忽略 */
    }
    streamRef.current = null
    mediaRef.current = null
    chunksRef.current = []
  }, [])

  /** 开始录音。返回是否真的在录（不支持/失败返回 false，绝不抛给调用方）。 */
  const start = useCallback(async () => {
    if (!supported) return false
    setError('')
    // 已经在录：先收尾，避免两段音频交叉
    if (mediaRef.current) return true
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { echoCancellation: true, noiseSuppression: true },
      })
      const mime = pickMime()
      const mr = mime ? new MediaRecorder(stream, { mimeType: mime }) : new MediaRecorder(stream)
      chunksRef.current = []
      mr.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) chunksRef.current.push(e.data)
      }
      mr.onerror = () => setError('录音采集异常，本题未保存录音')
      streamRef.current = stream
      mediaRef.current = mr
      startedAtRef.current = Date.now()
      mr.start(1000) // 每秒切一个 chunk，长回答也不会攒成一个巨大 blob
      setRecording(true)
      return true
    } catch (err) {
      cleanup()
      setRecording(false)
      setError(
        err?.name === 'NotAllowedError'
          ? '未获得麦克风权限，本题录音未保存（不影响面试）'
          : '无法开始录音，本题录音未保存（不影响面试）'
      )
      return false
    }
  }, [supported, cleanup])

  /**
   * 结束录音并返回 `{ blob, seconds }`（没有有效音频时返回 null）。
   * 调用方负责把 blob 交给 storage/recordings 落本机。
   */
  const stop = useCallback(
    () =>
      new Promise((resolve) => {
        const mr = mediaRef.current
        if (!mr) {
          setRecording(false)
          resolve(null)
          return
        }
        const seconds = Math.max(0, Math.round((Date.now() - startedAtRef.current) / 1000))
        // 兜底定时器：个别浏览器在轨道已停止时不会触发 onstop，
        // 没有它的话这个 Promise 永远不 resolve，而调用方在「结束回答」里 await 它 → 界面卡住。
        let done = false
        let timer = 0
        const finish = () => {
          if (done) return
          done = true
          clearTimeout(timer)
          const chunks = chunksRef.current
          cleanup()
          setRecording(false)
          if (!chunks.length) {
            resolve(null)
            return
          }
          try {
            resolve({ blob: new Blob(chunks, { type: chunks[0].type || 'audio/webm' }), seconds })
          } catch {
            resolve(null)
          }
        }
        timer = setTimeout(finish, 1500)
        try {
          mr.onstop = finish
          if (mr.state !== 'inactive') mr.stop()
          else finish()
        } catch {
          finish()
        }
      }),
    [cleanup]
  )

  /** 放弃当前录音（重录/切题时用）：不保存、不做转码 */
  const cancel = useCallback(() => {
    const mr = mediaRef.current
    try {
      if (mr && mr.state !== 'inactive') {
        mr.onstop = null
        mr.stop()
      }
    } catch {
      /* 忽略 */
    }
    cleanup()
    setRecording(false)
  }, [cleanup])

  useEffect(() => () => cancel(), [cancel])

  return { supported, recording, error, start, stop, cancel, setError }
}
