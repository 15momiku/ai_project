/**
 * 统一 API 客户端
 * - BASE = '' ：接口一律走同源相对路径 `/api/...`（开发由 Vite proxy 转到后端，
 *   生产由后端自己托管前端产物，同域部署，因此两套环境都不需要写死地址）
 * - 登录后所有请求自动带 `Authorization: Bearer <token>`；身份由后端按令牌解析，
 *   前端**不再**用 user_id 声明自己是谁（那是可伪造的，后端已一律忽略）
 * - 401（未登录 / 令牌失效）会清掉本地令牌并广播事件，由 AuthContext 引导去登录页
 * - 非 2xx 统一解析后端错误信息 + 触发 Toast，并把 Error 抛给调用方做兜底 UI
 */

const BASE = ''

/** 是否开发模式（`vite dev`）。用于只在本地成立的那几句排查提示：
 *  线上用户看到「请确认后端服务已启动（127.0.0.1:8000）」只会一头雾水，
 *  而开发者看到「请检查网络后重试」则少掉最关键的一条线索。 */
const IS_DEV = (() => {
  try {
    return Boolean(import.meta.env && import.meta.env.DEV)
  } catch {
    return false
  }
})()

/** fetch 直接抛错（断网 / 后端没起 / 被代理拦掉）时的提示 */
function networkErrorMessage() {
  return IS_DEV
    ? '网络连接失败，请确认后端服务已启动（127.0.0.1:8000）'
    : '网络连接失败，请检查网络后重试'
}

const TOKEN_KEY = 'aic_token'

/** 读取登录令牌（没有则空串 = 游客） */
export function getToken() {
  try {
    return localStorage.getItem(TOKEN_KEY) || ''
  } catch {
    return ''
  }
}

/** 保存登录令牌 */
export function setToken(token) {
  try {
    if (token) localStorage.setItem(TOKEN_KEY, token)
    else localStorage.removeItem(TOKEN_KEY)
  } catch {
    /* 隐私模式下忽略 */
  }
}

/** 清除登录令牌 */
export function clearToken() {
  setToken('')
}

/** 触发全局 Toast（Toast.jsx 监听该事件） */
export function notify(message, type = 'info', duration = 2800) {
  if (!message) return
  try {
    window.dispatchEvent(
      new CustomEvent('aic-toast', { detail: { message: String(message), type, duration } })
    )
  } catch {
    /* 忽略 */
  }
}

/** 广播「登录态失效」，AuthContext 监听后清理用户并跳登录页 */
function broadcastUnauthorized(message) {
  try {
    window.dispatchEvent(new CustomEvent('aic-unauthorized', { detail: { message } }))
  } catch {
    /* 忽略 */
  }
}

/** 把后端错误体转成一句人话 */
function pickError(payload, status) {
  if (!payload) return `请求失败（HTTP ${status}）`
  if (typeof payload === 'string') return payload.slice(0, 200)
  const detail = payload.detail ?? payload.message ?? payload.error
  if (typeof detail === 'string' && detail) return detail
  // FastAPI 校验错误：detail 是数组
  if (Array.isArray(detail)) {
    const first = detail[0]
    const loc = Array.isArray(first?.loc) ? first.loc.filter((x) => x !== 'body').join('.') : ''
    return `${loc ? loc + '：' : ''}${first?.msg || '参数校验失败'}`
  }
  return `请求失败（HTTP ${status}）`
}

/** 默认超时：普通接口 30s。
 *  启用大模型后，以下接口会等待 LLM 返回，耗时远高于普通接口，统一用 LONG_TIMEOUT：
 *  简历/JD 结构化解析、出题与追问、报告生成与二次评分。
 *  免费/轻量模型（如 glm-4-flash）解析整份简历要 70-90 秒，30s 会误报「请求超时」。 */
const DEFAULT_TIMEOUT = 30000
export const LONG_TIMEOUT = 180000

async function request(method, path, body, opts = {}) {
  const { silent = false, formData = null, query = null, timeout = DEFAULT_TIMEOUT } = opts
  let url = BASE + path
  if (query && typeof query === 'object') {
    const qs = new URLSearchParams()
    Object.entries(query).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== '') qs.append(k, String(v))
    })
    const s = qs.toString()
    if (s) url += (url.includes('?') ? '&' : '?') + s
  }

  const init = { method, headers: {} }
  // 身份凭据：有令牌就带上。后端只认这个头，请求体里的 user_id 一概忽略。
  const token = getToken()
  if (token) init.headers.Authorization = `Bearer ${token}`
  if (formData) {
    init.body = formData
  } else if (body !== undefined && body !== null) {
    init.headers['Content-Type'] = 'application/json'
    init.body = JSON.stringify(body)
  }

  // 超时控制：没有 AbortController 时后端一旦挂起（例如 LLM 卡住），
  // 页面会永远停在 loading 且没有任何重试入口。
  const controller = typeof AbortController !== 'undefined' ? new AbortController() : null
  let timedOut = false
  let timer = null
  if (controller) {
    init.signal = controller.signal
    timer = setTimeout(() => {
      timedOut = true
      controller.abort()
    }, Math.max(1000, Number(timeout) || DEFAULT_TIMEOUT))
  }

  let res
  try {
    res = await fetch(url, init)
  } catch (err) {
    if (timedOut) {
      const msg = `请求超时（${Math.round((Number(timeout) || DEFAULT_TIMEOUT) / 1000)}s），请稍后重试`
      if (!silent) notify(msg, 'error', 3600)
      const e = new Error(msg)
      e.timeout = true
      throw e
    }
    const msg = networkErrorMessage()
    if (!silent) notify(msg, 'error', 3600)
    throw new Error(msg)
  } finally {
    if (timer) clearTimeout(timer)
  }

  const text = await res.text()
  let data = null
  let parsed = true
  if (text) {
    try {
      data = JSON.parse(text)
    } catch {
      parsed = false
      data = text
    }
  }

  if (!res.ok) {
    const msg = pickError(data, res.status)
    // 401 = 未登录或令牌已失效：清掉本地令牌并广播，让 AuthContext 把我带到登录页。
    // 否则用户会一直卡在「操作失败」却不知道为什么。
    if (res.status === 401) {
      clearToken()
      broadcastUnauthorized(msg)
    }
    if (!silent) notify(msg, 'error', 3600)
    const err = new Error(msg)
    err.status = res.status
    err.payload = data
    throw err
  }

  // 业务接口一律返回 JSON。若拿到 HTML/纯文本（200），说明请求被静态服务器或 SPA 首页接管，
  // 此时把它当成功返回会让页面静默使用兜底数据、并把运行模式显示错。
  // 开发模式下最可能的原因是 Vite 代理没指向后端，因此只在开发模式里给出这条线索。
  if (path.startsWith('/api') && !parsed) {
    const msg = IS_DEV
      ? '后端返回了非 JSON 响应，请确认 API 代理指向 127.0.0.1:8000'
      : '服务返回了非预期格式的响应，请稍后重试'
    if (!silent) notify(msg, 'error', 3600)
    const err = new Error(msg)
    err.status = res.status
    err.payload = data
    throw err
  }
  return data
}

export const get = (path, query, opts) => request('GET', path, null, { ...opts, query })
export const post = (path, body, opts) => request('POST', path, body, opts)
export const put = (path, body, opts) => request('PUT', path, body, opts)
export const patch = (path, body, opts) => request('PATCH', path, body, opts)
export const del = (path, opts) => request('DELETE', path, null, opts)

/* ==========================================================================
   认证
   ========================================================================== */

/* /auth/me：未登录返回游客身份；令牌失效返回 401（由 request 统一清理并广播） */
export const authRegister = (username, password, nickname = '', school = '') =>
  post('/api/auth/register', { username, password, nickname, school })

export const authLogin = (username, password) => post('/api/auth/login', { username, password })

export const authLogout = () => post('/api/auth/logout', {})

/* /auth/me：未登录返回游客身份；令牌失效返回 401（由 request 统一清理并广播） */
export const authMe = (opts) => get('/api/auth/me', null, opts)

export const authStatus = (opts) => get('/api/auth/status', null, opts)

/* ==========================================================================
   元信息
   ========================================================================== */
export const getHealth = () => get('/api/health')
export const getMetaOptions = () => get('/api/meta/options')
export const demoSeed = () => post('/api/demo/seed', {})

/* ==========================================================================
   档案：简历 / JD / 公司 / 匹配
   ========================================================================== */
export function uploadResume(file) {
  const fd = new FormData()
  fd.append('file', file)
  return post('/api/resume/upload', null, { formData: fd, timeout: LONG_TIMEOUT })
}

export const getResume = (resumeId) => get(`/api/resume/${resumeId}`)
export const updateResume = (resumeId, parsed) => put(`/api/resume/${resumeId}`, { parsed })

/** 没有 PDF 时直接粘贴简历文本，走与 PDF 完全相同的结构化解析 */
export const parseResumeText = (text) =>
  post('/api/resume/text', { text }, { timeout: LONG_TIMEOUT })

export const parseJd = (text, jobTitle = '') =>
  post('/api/jd/parse', { text, job_title: jobTitle }, { timeout: LONG_TIMEOUT })

export function uploadJd(file) {
  const fd = new FormData()
  fd.append('file', file)
  return post('/api/jd/upload', null, { formData: fd, timeout: LONG_TIMEOUT })
}

export const getJd = (jdId) => get(`/api/jd/${jdId}`)
export const updateJd = (jdId, parsed) => put(`/api/jd/${jdId}`, { parsed })
/** 我解析过的岗位列表（体验增强 §8：同一份简历挂多个 JD 做对比） */
export const listJds = (limit = 20) => get('/api/jd/list', { limit })

export const searchCompany = (q, limit = 8) => get('/api/company/search', { q, limit })
export const getCompany = (companyId) => get(`/api/company/${companyId}`)
export const manualCompany = (payload) => post('/api/company/manual', payload)

export const computeMatch = (payload) => post('/api/match', payload)

/* ==========================================================================
   面试会话
   ========================================================================== */
/* 出题与追问都要等 LLM 生成，用宽松超时，否则会误报「请求超时」*/
export const interviewStart = (payload) =>
  post('/api/interview/start', payload, { timeout: LONG_TIMEOUT })
export const interviewAnswer = (sessionId, payload) =>
  post(`/api/interview/${sessionId}/answer`, payload, { timeout: LONG_TIMEOUT })
export const interviewSkip = (sessionId, questionId) =>
  post(`/api/interview/${sessionId}/skip`, { question_id: questionId }, { timeout: LONG_TIMEOUT })
export const getInterviewState = (sessionId) => get(`/api/interview/${sessionId}`)

/**
 * 上报单题口播指标（体验增强 §5）。
 *
 * 服务端只落库、不评分，并用同一套阈值重算后回传，
 * 因此 `silent: true`：这条请求失败不该打扰用户（观测数据，不影响答题）。
 */
export const saveSpeechMetrics = (sessionId, payload) =>
  post(`/api/interview/${sessionId}/speech-metrics`, payload, { silent: true })

/* ==========================================================================
   语音
   ========================================================================== */
export const ttsSynthesize = (text, voice = 'female', rate = '+0%') =>
  post('/api/tts/synthesize', { text, voice, rate }, { silent: true })

/** 后端当前生效的语音提供方：{ provider, asr_provider, voices }。
 *  前端据此决定走阿里云一键识别（aliyun）还是浏览器 Web Speech API（browser）。 */
export const getMediaVoices = () => get('/api/media/tts/voices')

/* ASR：上传音频做一句话识别（前端采集 16k 单声道 PCM）。
 * 后端 provider=aliyun 时返回识别文本；provider=browser 时前端走 Web Speech API。 */
export const asrRecognize = (pcmBlob, filename = 'audio.pcm') => {
  const formData = new FormData()
  formData.append('audio', pcmBlob, filename)
  return request('POST', '/api/asr/recognize', null, {
    formData,
    silent: true,
    timeout: 60000,
  })
}

/* ==========================================================================
   报告
   ==========================================================================
   报告接口可能触发 LLM 评分与二次校验，耗时远高于普通接口，统一放宽超时。 */
export const getReport = (sessionId) => get(`/api/report/${sessionId}`, null, { timeout: LONG_TIMEOUT })

/**
 * 历史报告列表（最近 10 场已生成报告的面试）。
 *
 * 报告是**按场次**的：只有跑完一场面试才会有报告。此前唯一的入口是「答完自动跳转」，
 * 一旦离开报告页就再也回不去（接口一直存在，只是没人调）。首页的「历史报告」用它兜住回路。
 * 只返回当前登录用户的数据，后端按 created_at 升序给出，前端展示时倒序（最近的在最前）。
 */
export const getReportHistory = () => get('/api/report/history')
export const generateReport = (sessionId) =>
  post(`/api/report/${sessionId}/generate`, {}, { timeout: LONG_TIMEOUT })
export const exportReport = (sessionId, format = 'markdown') =>
  get(`/api/report/${sessionId}/export`, { format }, { timeout: LONG_TIMEOUT })
export const reportFeedback = (sessionId, helpful, comment = '') =>
  post(`/api/report/${sessionId}/feedback`, { helpful, comment })

/**
 * 逐题评分反馈（体验增强 §6）：准 / 偏高 / 偏低。
 *
 * 这是低成本高价值的闭环——用户能质疑评分，反馈回灌后用于校准评分 Prompt。
 */
export const reportQuestionFeedback = (sessionId, questionIndex, verdict) =>
  post(`/api/report/${sessionId}/feedback`, {
    question_index: questionIndex,
    verdict,
  })
export const getShare = (sessionId) => get(`/api/report/${sessionId}/share`, null, { timeout: LONG_TIMEOUT })

/**
 * 报告生成进度（生成过程可视化）。
 *
 * 轮询接口，必须 **silent**：生成期间每秒都会调一次，一旦抽风就会连续弹 Toast；
 * 进度拿不到不是错误（多进程部署 / 尚未开始），页面照常显示不确定进度即可。
 * 超时也收紧到 5s：它只是读内存，慢就意味着这次读不到，不该拖住整个轮询。
 */
export const getReportProgress = (sessionId) =>
  get(`/api/report/${sessionId}/progress`, null, { timeout: 5000, silent: true })

/* ==========================================================================
   错题本 / 排行榜
   ========================================================================== */
export const getWrongQuestions = (category = '') =>
  get('/api/practice/wrong-questions', { category })
/**
 * 重做错题（体验增强 §9）。
 *
 * `startMicro=true` 时后端会用原题的**同一环节**建一场 3 题微面试，
 * 并在 `micro` 里返回会话 id 与该维度的历史得分（前后对比基准）。
 */
export const retryWrong = (wrongId, startMicro = false) =>
  post('/api/practice/retry', { wrong_id: wrongId, start_micro: startMicro })
export const deleteWrong = (wrongId) => del(`/api/practice/wrong-questions/${wrongId}`)

export const getLeaderboard = (type = 'job') => get('/api/leaderboard', { type })

/**
 * 我的成长面板（体验增强 §12）：连续打卡、近 7 天格子、近 4 周汇总、维度首末对比。
 * 只统计当前用户已完成的面试。
 */
export const getGrowthSummary = () => get('/api/growth/summary')

/* ==========================================================================
   简历优化（润色 / ATS 检测 / PDF 解析 / PDF 导出）
   ==========================================================================
   全部是无状态计算接口：不落库，刷新页面即重来。
   润色与 ATS 都要等大模型返回，统一用 LONG_TIMEOUT。
   登录用户不限次数：接口既不计数、也不会返回「次数不足」。 */
export const getResumeOptimizerMeta = () => get('/api/resume-optimizer/meta')

export const polishResume = (resume, jd = '', language = 'zh') =>
  post('/api/resume-optimizer/polish', { resume, jd, language }, { timeout: LONG_TIMEOUT })

export const checkResumeAts = (resume, jd = '') =>
  post('/api/resume-optimizer/ats', { resume, jd }, { timeout: LONG_TIMEOUT })

/**
 * JD 关键词覆盖率（体验增强 §11）。
 *
 * 后端是**纯规则计算、不走大模型**，因此零密钥环境也能用，
 * 也不会产生 LLM 调用费用，可以随输入自动重算。
 */
export const getKeywordCoverage = (resume, jd = '') =>
  post('/api/resume-optimizer/keyword-coverage', { resume, jd })

export function uploadResumeForOptimize(file) {
  const fd = new FormData()
  fd.append('file', file)
  return post('/api/resume-optimizer/upload-pdf', null, { formData: fd, timeout: LONG_TIMEOUT })
}

/**
 * 导出简历 PDF。
 *
 * 这里没有走 request()：那条链路只处理 JSON，而本接口返回二进制流，
 * 并且要在响应头里读 X-Resume-Pages（判断是否需要提醒用户精简内容）。
 * 失败时仍然沿用统一的 Toast + Error 语义。
 */
export async function renderResumePdf(resume, templateName = 'template1.html', language = 'zh') {
  const init = {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ resume, template_name: templateName, language }),
  }
  const token = getToken()
  if (token) init.headers.Authorization = `Bearer ${token}`

  let res
  try {
    res = await fetch(`${BASE}/api/resume-optimizer/render-pdf`, init)
  } catch {
    const msg = networkErrorMessage()
    notify(msg, 'error', 3600)
    throw new Error(msg)
  }

  if (!res.ok) {
    let payload = null
    try {
      payload = JSON.parse(await res.text())
    } catch {
      /* 非 JSON 错误体（例如代理返回的 HTML），回退到状态码文案 */
    }
    const msg = pickError(payload, res.status)
    notify(msg, 'error', 3600)
    const err = new Error(msg)
    err.status = res.status
    throw err
  }

  return {
    blob: await res.blob(),
    pages: Number(res.headers.get('X-Resume-Pages')) || 1,
    baseFont: Number(res.headers.get('X-Resume-Base-Font')) || 0,
    filename: templateName.replace(/\.html$/i, '') + '.pdf',
  }
}

/* ==========================================================================
   岗位信息汇总（共享岗位库 + 各人独立投递进度）
   ==========================================================================
   产品口径：**岗位信息全员共享，投递进度各人独立**。

   · 整个模块要求登录（后端 `require_login`）：本项目的游客走进程内内存库，
     岗位库在那个库里是空的，放行只会得到一个「岗位库是空的」的假象，
     也会与「游客模式下不展示任何数据」的口径自相矛盾。因此前端也把这一页
     对游客替换成说明卡（见页面的 LoginRequired）。
   · 岗位库维护（新增/导入/示例/清空/删除）同样只要求登录——没有单独的管理员体系，
     「谁能改岗位库」这件事不再由前端藏按钮来表达（隐藏按钮本来也不是权限）。
   · 进度只能改自己的：归属由令牌决定，请求体里没有、也不接受 user_id。

   导出（.xlsx）在前端用零依赖的 lib/xlsx.js 完成，因此这里没有导出接口：
   数据已经在页面上了，为它多走一趟服务端只会多一个失败点。
   ========================================================================== */

/** 岗位库列表 + 我的投递进度 + 状态字典（一次请求拿全页面要用的东西） */
export const listJobs = (opts) => get('/api/jobs', null, opts)

/** 投递统计（只统计「投递进度不为未投递」的岗位；聚合口径在服务端唯一实现） */
export const getJobStats = (opts) => get('/api/jobs/stats', null, opts)

/** 设置我的投递进度；`status` 传「未投递」= 清除该岗位的投递记录 */
export const setJobProgress = (jobId, status) =>
  put(`/api/jobs/${encodeURIComponent(jobId)}/progress`, { status })

/** 新增 / 修改一条岗位（需登录）。字段与后端 JobUpsertRequest 对齐。 */
export const createJob = (payload) => post('/api/jobs', payload)

export const updateJob = (jobId, payload) => put(`/api/jobs/${encodeURIComponent(jobId)}`, payload)

/**
 * 批量导入岗位（需登录）。
 *
 * `dedupe` 默认 **false（不去重）**：与后端 `JobBulkRequest.dedupe` 的默认值保持一致 ——
 * 一份校招汇总表里同一家企业因不同批次 / 不同岗位方向重复出现是常态，
 * 默认去重会把后面的行静默吃掉。两端默认值必须相同，否则「界面没勾选」
 * 与「后端按默认值处理」会各说各话。
 */
export const bulkImportJobs = (jobs, dedupe = false) => post('/api/jobs/bulk', { jobs, dedupe })

/** 载入内置的 20 条示例岗位（需登录，按企业名称去重） */
export const seedJobs = () => post('/api/jobs/seed', {})

/** 删除单条岗位（需登录，连带清除所有人在它上面的进度） */
export const deleteJob = (jobId) => del(`/api/jobs/${encodeURIComponent(jobId)}`)

/** 清空岗位库（需登录，连带清除所有人的相关进度） */
export const clearJobs = () => del('/api/jobs')

/* ==========================================================================
   本地工具
   ========================================================================== */
export function loadLocal(key, fallback) {
  try {
    const raw = localStorage.getItem(key)
    return raw ? JSON.parse(raw) : fallback
  } catch {
    return fallback
  }
}

export function saveLocal(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value))
  } catch {
    /* 忽略 */
  }
}
