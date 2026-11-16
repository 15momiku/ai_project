import { useCallback, useEffect, useRef, useState } from 'react'
import { asrRecognize } from '../api/client'

/**
 * 语音识别（ASR）Hook
 *
 * 双链路：
 *  1) cloud（阿里云一句号识别，asrProvider==='aliyun'）
 *     - WebAudio 采集麦克风 → 重采样为 16kHz 单声道 PCM16
 *     - stop() 时把 PCM 上传后端 /api/asr/recognize 识别，返回文本
 *  2) browser（浏览器原生 Web Speech API，默认/兜底）
 *     仅在未启用云端时使用，成本为零但依赖浏览器。
 *
 * 对外接口：supported / transcript / interim /
 * seconds / error / start / stop / reset / setTranscript / setError。
 * 注意：cloud 模式下 stop() 返回 Promise（异步识别）；browser 模式返回 string，
 * 调用方用 `await rec.stop()` 两种模式都通用。
 */

/* 采样率 16k、16bit 单声道的裸 PCM（无 WAV 头） */
function pcmToBlob(frames) {
  const buffer = new ArrayBuffer(frames.length * 2)
  const view = new DataView(buffer)
  for (let i = 0; i < frames.length; i += 1) {
    const s = Math.max(-1, Math.min(1, frames[i]))
    view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true)
  }
  return new Blob([buffer], { type: 'audio/pcm' })
}

export default function useRecorder(asrProvider = 'browser') {
  const useCloud = asrProvider === 'aliyun'

  const SR =
    typeof window !== 'undefined'
      ? window.SpeechRecognition || window.webkitSpeechRecognition
      : null

  // cloud 模式即使没有 Web Speech API 也可用（走 WebAudio 采集 + 后端识别）
  const supported = useCloud || !!SR
  const [transcript, setTranscriptState] = useState('')
  const [interim, setInterim] = useState('')
  const [seconds, setSeconds] = useState(0)
  const [error, setError] = useState('')

  const recRef = useRef(null)
  const wantRef = useRef(false)
  const timerRef = useRef(0)
  const finalRef = useRef('')
  const interimRef = useRef('')
  const secRef = useRef(0)

  // ---- cloud 链路内部状态（均为 ref，避免渲染抖动） ----
  const cloudCtxRef = useRef(null)
  const cloudStreamRef = useRef(null)
  const cloudFramesRef = useRef([])

  const clearTimer = () => {
    clearInterval(timerRef.current)
    timerRef.current = 0
  }

  /* ---------------- browser 链路 ---------------- */
  const buildRecognition = useCallback(() => {
    const rec = new SR()
    rec.lang = 'zh-CN'
    rec.continuous = true
    rec.interimResults = true
    rec.maxAlternatives = 1

    rec.onresult = (event) => {
      let interimText = ''
      let finalText = ''
      for (let i = event.resultIndex; i < event.results.length; i += 1) {
        const res = event.results[i]
        const text = res[0]?.transcript || ''
        if (res.isFinal) finalText += text
        else interimText += text
      }
      if (finalText) {
        finalRef.current = (finalRef.current + finalText).trimStart()
        setTranscriptState(finalRef.current)
      }
      interimRef.current = interimText
      setInterim(interimText)
    }

    rec.onerror = (e) => {
      const map = {
        'not-allowed': '麦克风权限被拒绝，请在浏览器设置中允许后重试，或切换文字模式',
        'service-not-allowed': '浏览器语音识别服务不可用，请切换文字模式',
        'audio-capture': '未检测到麦克风设备，请切换文字模式',
        network: '语音识别网络异常，请检查网络或切换文字模式',
        'no-speech': '没有听到声音，请靠近麦克风再试一次',
        aborted: '',
      }
      const msg = map[e.error]
      if (msg !== '') setError(msg || `语音识别异常（${e.error || '未知'}）`)
      if (e.error === 'not-allowed' || e.error === 'service-not-allowed' || e.error === 'audio-capture') {
        wantRef.current = false
        clearTimer()
      }
    }

    rec.onend = () => {
      setInterim('')
      // continuous 模式下浏览器仍可能自动断开：只要用户还没点停止就自动续听
      if (wantRef.current) {
        try {
          rec.start()
          return
        } catch {
          /* 忽略重启失败 */
        }
      }
      clearTimer()
    }

    return rec
  }, [SR])
  /* ---------------- cloud 链路 ---------------- */
  const startCloud = useCallback(async () => {
    setError('')
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      })
      const Ctx = window.AudioContext || window.webkitAudioContext
      // 直接让 AudioContext 把音频重采样到 16kHz 单声道，避免手动线性重采样漏采样
      const ctx = new Ctx({ sampleRate: 16000 })
      await ctx.resume() // 关键：不 resume 时 onaudioprocess 不触发，录到的是空气
      const source = ctx.createMediaStreamSource(stream)

      const node = ctx.createScriptProcessor(4096, 1, 1)
      const parts = []
      cloudFramesRef.current = parts
      node.onaudioprocess = (e) => {
        const data = e.inputBuffer.getChannelData(0)
        parts.push(new Float32Array(data)) // 拷贝一份，buffer 会被复用
      }

      source.connect(node)
      // 接一个静音节点而非 destination，避免把麦克风声音外放造成啸叫/回声
      const mute = ctx.createGain()
      mute.gain.value = 0
      node.connect(mute)
      mute.connect(ctx.destination)

      cloudCtxRef.current = ctx
      cloudStreamRef.current = stream

      return true
    } catch (err) {
      setError(
        err?.name === 'NotAllowedError'
          ? '麦克风权限被拒绝，请在浏览器设置中允许后重试，或切换文字模式'
          : '无法使用麦克风，请检查设备或切换文字模式'
      )
      return false
    }
  }, [])

  const stopCloud = useCallback(async () => {
    const ctx = cloudCtxRef.current
    const stream = cloudStreamRef.current
    if (ctx) {
      try {
        ctx.close()
      } catch {
        /* 忽略 */
      }
    }
    stream?.getTracks()?.forEach((t) => t.stop())
    cloudCtxRef.current = null
    cloudStreamRef.current = null

    // 合并所有采到的 16k 单声道 float 帧
    const parts = Array.isArray(cloudFramesRef.current) ? cloudFramesRef.current : []
    cloudFramesRef.current = []
    const total = parts.reduce((n, p) => n + p.length, 0)
    const frames = new Float32Array(total)
    let off = 0
    for (const p of parts) {
      frames.set(p, off)
      off += p.length
    }

    if (frames.length < 8000) {
      // 太少音频（<0.5s）视为无效，交给页面提示
      return finalRef.current || ''
    }
    try {
      const pcm = pcmToBlob(frames)
      const res = await asrRecognize(pcm, 'audio.pcm')
      if (res?.provider === 'aliyun' && res.text) {
        finalRef.current = res.text.trim()
        setTranscriptState(finalRef.current)
      } else if (res?.provider !== 'aliyun') {
        setError('云端识别暂不可用，请重试或切换文字模式')
      }
      return finalRef.current || ''
    } catch (err) {
      setError('语音识别失败，请检查网络或切换文字模式')
      return finalRef.current || ''
    }
  }, [])

  /* ---------------- 统一接口 ---------------- */
  const start = useCallback(() => {
    setError('')
    if (useCloud) {
      wantRef.current = true
      clearTimer()
      startCloud()
      timerRef.current = setInterval(() => {
        secRef.current += 1
        setSeconds(secRef.current)
      }, 1000)
      return true
    }
    if (!supported) {
      setError('当前浏览器不支持语音识别，已建议使用文字模式')
      return false
    }
    wantRef.current = true
    if (!recRef.current) recRef.current = buildRecognition()
    try {
      recRef.current.start()
    } catch {
      /* 已在识别中 */
    }
    clearTimer()
    timerRef.current = setInterval(() => {
      secRef.current += 1
      setSeconds(secRef.current)
    }, 1000)
    return true
  }, [useCloud, supported, startCloud, buildRecognition])

  const stop = useCallback(() => {
    wantRef.current = false
    clearTimer()

    // cloud：异步识别，返回 Promise
    if (useCloud) {
      const extra = (interimRef.current || '').trim()
      if (extra) {
        finalRef.current = (finalRef.current + extra).trimStart()
        setTranscriptState(finalRef.current)
      }
      return stopCloud()
    }

    // browser：同步返回
    const extra = (interimRef.current || '').trim()
    if (extra) {
      finalRef.current = (finalRef.current + extra).trimStart()
      setTranscriptState(finalRef.current)
    }
    interimRef.current = ''
    setInterim('')
    try {
      recRef.current?.stop()
    } catch {
      /* 忽略 */
    }
    return finalRef.current
  }, [useCloud, stopCloud])

  /** 清空（含计时与中间结果） */
  const reset = useCallback(() => {
    finalRef.current = ''
    interimRef.current = ''
    secRef.current = 0
    setTranscriptState('')
    setInterim('')
    setSeconds(0)
    setError('')
    clearTimer()
  }, [])

  /** 手动编辑转写文本（ASR 不准时用户可修正） */
  const setTranscript = useCallback((text) => {
    finalRef.current = typeof text === 'string' ? text : ''
    setTranscriptState(finalRef.current)
  }, [])

  useEffect(
    () => () => {
      wantRef.current = false
      clearTimer()
      try {
        recRef.current?.abort?.()
      } catch {
        /* 忽略 */
      }
      if (cloudStreamRef.current) {
        cloudStreamRef.current.getTracks()?.forEach((t) => t.stop())
      }
      if (cloudCtxRef.current) {
        try {
          cloudCtxRef.current.close()
        } catch {
          /* 忽略 */
        }
      }
    },
    []
  )

  return {
    supported,
    transcript,
    interim,
    seconds,
    error,
    start,
    stop,
    reset,
    setTranscript,
    setError,
  }
}