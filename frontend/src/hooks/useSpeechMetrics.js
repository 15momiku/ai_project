import { useMemo } from 'react'

/**
 * 口播指标本地计算（体验增强 §5 口播实时反馈）
 *
 * 设计原则：**实时只给事实，评分仍走六维模型**。
 * 这里只算 语速 / 填充词 / 时长，不算任何分数或等级 —— 用户不该「边答边掉分」。
 *
 * 阈值与后端 `backend/app/constants.py` 严格一致（那里是唯一口径来源），
 * 否则会出现「前端说偏快、后端说正常」的分裂结论：
 *   SPEECH_RATE_IDEAL_MIN = 180 字/分
 *   SPEECH_RATE_IDEAL_MAX = 260 字/分
 *   SPEECH_RATE_LOW       = 120 字/分
 *   SPEECH_FOCUS_SECONDS  = 150 秒
 *   FILLER_WORDS          = 见下方 FILLER_WORDS
 *
 * 用法：
 *   const metrics = useSpeechMetrics(transcript, seconds)
 *   // { chars, charsPerMinute, fillerCount, fillerWords, level, note, focusHint }
 */

/** 与后端 `constants.FILLER_WORDS` 完全一致（顺序也保持一致，便于逐项对照） */
export const FILLER_WORDS = ['然后', '就是', '嗯', '那个', '其实', '怎么说', '反正', '所以说']

export const SPEECH_RATE_IDEAL_MIN = 180
export const SPEECH_RATE_IDEAL_MAX = 260
export const SPEECH_RATE_LOW = 120
export const SPEECH_FOCUS_SECONDS = 150

/**
 * 提示词优先级：同一时刻只说一句最该说的话。
 * 后端会把多项拼成一句（note 字段原样上报），这里挑最靠前的一项做界面提示，
 * 避免手机上堆三句话；填充词由反馈条单独作为事实展示，不再进 note 造成重复。
 */
const NOTE_TEXT = {
  low: '内容偏少，可以再展开一些细节',
  slow: '语速偏慢，可以稍微提一点节奏',
  fast: '语速偏快，注意留出停顿',
  long: '单题偏长，下次可以更聚焦',
}

export default function useSpeechMetrics(transcript, seconds) {
  return useMemo(() => {
    const text = typeof transcript === 'string' ? transcript : ''
    const secs = Math.max(0, Math.floor(Number(seconds) || 0))
    const chars = text.length

    // 语速 = 转写字符数 ÷ (秒数 / 60)。
    // 秒数 ≤ 0 时返回 0 且 level = 'good'：表示「无法计算」，
    // 绝不因为 1 秒里说了 2 个字就报「极快」，那只是采样还没开始。
    const cpm = secs > 0 && chars > 0 ? Math.round((chars / (secs / 60)) * 10) / 10 : 0

    let level = 'good'
    if (cpm > 0) {
      if (cpm < SPEECH_RATE_IDEAL_MIN) level = 'slow'
      else if (cpm > SPEECH_RATE_IDEAL_MAX) level = 'fast'
    }

    // 填充词：统计**总次数**（「然后」说 3 次就是 3 次，不是去重后的 1），
    // 同时回传命中的词，供界面原样展示「那个/就是」。
    let fillerCount = 0
    const fillerWords = []
    if (text) {
      for (const word of FILLER_WORDS) {
        let from = text.indexOf(word)
        if (from < 0) continue
        fillerWords.push(word)
        while (from >= 0) {
          fillerCount += 1
          from = text.indexOf(word, from + word.length)
        }
      }
    }

    const tooLong = secs > SPEECH_FOCUS_SECONDS

    let note = ''
    if (cpm > 0) {
      if (cpm < SPEECH_RATE_LOW) note = NOTE_TEXT.low
      else if (cpm < SPEECH_RATE_IDEAL_MIN) note = NOTE_TEXT.slow
      else if (cpm > SPEECH_RATE_IDEAL_MAX) note = NOTE_TEXT.fast
    }
    // 语速本身没问题、但单题拖太久时才提时长（一次只说一句）
    if (!note && tooLong) note = NOTE_TEXT.long

    return {
      chars,
      durationSeconds: secs,
      charsPerMinute: cpm,
      fillerCount,
      fillerWords,
      level,
      note,
      // 阈值标志位：note 一旦被语速占用，父组件仍能独立知道「这题超时了」
      focusHint: tooLong,
    }
  }, [transcript, seconds])
}
