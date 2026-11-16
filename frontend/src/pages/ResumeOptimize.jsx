import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import SectionCard from '../components/SectionCard'
import ScoreRing from '../components/ScoreRing'
import LoginRequired from '../components/LoginRequired'
import { useAuth } from '../auth/AuthContext'
import {
  IconCheckCircle,
  IconDownload,
  IconInfo,
  IconPhoto,
  IconPlus,
  IconScanCheck,
  IconSparkle,
  IconTrash,
  IconUpload,
  IconWand,
  IconWarn,
} from '../components/icons'
import {
  checkResumeAts,
  getKeywordCoverage,
  getResumeOptimizerMeta,
  notify,
  polishResume,
  renderResumePdf,
  uploadResumeForOptimize,
} from '../api/client'
import './resume-optimize.css'

/* ==========================================================================
   常量与纯函数
   ========================================================================== */

/** 四类经历：字段语义一致（company / title / 起止时间 / 描述），只是叫法不同。
 *  tone 复用全局的维度色令牌，让四类经历在同一套配色体系里可区分。 */
const EXPERIENCE_SECTIONS = [
  {
    key: 'education',
    index: '③',
    title: '教育经历',
    tone: 'tone-blue',
    hint: '教育背景的描述保持普通单段即可（AI 只对工作/项目/校园经历做 STAR 分段）',
    fields: [
      { name: 'company', label: '学校', placeholder: '如：华南理工大学' },
      { name: 'title', label: '专业', placeholder: '如：软件工程' },
    ],
    descPlaceholder: '如：主修数据结构、操作系统，做过几个课程项目',
  },
  {
    key: 'work_experience',
    index: '④',
    title: '工作经历',
    tone: 'tone-teal',
    hint: '用口语写就行，AI 会润色并拆成「背景 / 行动 / 结果」三段',
    fields: [
      { name: 'company', label: '公司', placeholder: '如：腾讯' },
      { name: 'title', label: '职位', placeholder: '如：后端开发工程师' },
    ],
    descPlaceholder: '如：负责后台系统开发，用 Java 和 Spring，扛住了高并发',
  },
  {
    key: 'project_experience',
    index: '⑤',
    title: '项目经历',
    tone: 'tone-violet',
    hint: '写清「做了什么 + 用了什么技术 + 结果如何」，量化数据越具体越好',
    fields: [
      { name: 'title', label: '项目名称', placeholder: '如：AI 简历生成器' },
      { name: 'company', label: '组织 / 客户', placeholder: '如：个人项目' },
    ],
    descPlaceholder: '如：用 FastAPI 写了一个简历生成工具，接大模型润色简历',
  },
  {
    key: 'campus_experience',
    index: '⑥',
    title: '校园经历',
    tone: 'tone-magenta',
    hint: '学生会、社团、志愿活动都可以写，突出你的角色与产出',
    fields: [
      { name: 'company', label: '组织 / 社团', placeholder: '如：校学生会技术部' },
      { name: 'title', label: '角色 / 职务', placeholder: '如：技术负责人' },
    ],
    descPlaceholder: '如：负责维护学生会网站，带 5 个同学做活动报名系统',
  },
]

/** 技能常用词：点一下就加，避免用户手敲 */
const SKILL_SUGGESTIONS = ['Python', 'Java', 'JavaScript', 'SQL', 'FastAPI', 'React', 'Git', 'Linux', 'Excel', 'Figma']

const PHOTO_W = 300
const PHOTO_H = 420 // 一寸 25×35mm 的 5:7 比例

const emptyItem = () => ({ title: '', company: '', start_date: '', end_date: '', description: '' })
const emptyHonor = () => ({ date: '', name: '', level: '' })

/** 表单骨架：字段名与后端 ResumeData 严格一致 */
function blankResume(base) {
  return {
    ...(base || {}),
    name: '', phone: '', email: '', job_intention: '', age: '', gender: '',
    ethnicity: '', wechat: '', political_status: '', slogan: '', photo: '',
    education: [emptyItem()],
    work_experience: [emptyItem()],
    project_experience: [emptyItem()],
    campus_experience: [emptyItem()],
    honors: [emptyHonor()],
    skills: [],
  }
}

/** 示例简历：给「没有旧简历」的用户一个可跑通的起点，而不是预填假数据 */
function demoResume(base) {
  return {
    ...blankResume(base),
    name: '林宇航',
    phone: '13800138000',
    email: 'linyh@example.com',
    job_intention: '后端开发工程师',
    age: '22',
    gender: '男',
    ethnicity: '汉',
    wechat: 'linyh_wx',
    political_status: '共青团员',
    slogan: '专注后端开发 · 追求极致的工程效率',
    education: [
      {
        title: '软件工程',
        company: '华南理工大学',
        start_date: '2021-09',
        end_date: '2025-06',
        description: '主修数据结构、操作系统、数据库；GPA 3.7/4.0，专业前 10%',
      },
    ],
    work_experience: [
      {
        title: '后端开发实习生',
        company: '某互联网公司',
        start_date: '2024-07',
        end_date: '2024-10',
        description: '给订单服务加了缓存和限流，写了一些接口文档',
      },
    ],
    project_experience: [
      {
        title: '校园二手交易平台',
        company: '课程项目',
        start_date: '2023-09',
        end_date: '2024-01',
        description: '用 FastAPI + Vue 做了个二手交易平台，负责后端和部署',
      },
    ],
    campus_experience: [
      {
        title: '技术负责人',
        company: '校学生会技术部',
        start_date: '2022-09',
        end_date: '2023-06',
        description: '带 5 人小组维护学生会网站，做了活动报名系统',
      },
    ],
    honors: [
      { date: '2023-10', name: '国家奖学金', level: '国家级' },
      { date: '2023-05', name: '蓝桥杯程序设计大赛', level: '省级二等奖' },
    ],
    skills: ['Python', 'Java', 'FastAPI', 'MySQL', 'Git'],
  }
}

/** 表单里是否已经有可润色的内容 */
function hasContent(r) {
  if (!r) return false
  if (String(r.name || '').trim()) return true
  if ((r.skills || []).length) return true
  return EXPERIENCE_SECTIONS.some((sec) =>
    (r[sec.key] || []).some((it) => Object.values(it || {}).some((v) => String(v || '').trim()))
  )
}

/** 后端返回的条目只保留表单字段：description_lines 是给 Jinja 模板用的渲染中间产物，
 *  留在前端状态里只会被下一次请求原样带回去。 */
const pickItem = (it) => ({
  title: it?.title || '',
  company: it?.company || '',
  start_date: it?.start_date || '',
  end_date: it?.end_date || '',
  description: it?.description || '',
})
const pickHonor = (h) => ({ date: h?.date || '', name: h?.name || '', level: h?.level || '' })

/** 把后端结果合并成一份合法的表单状态（四类经历/荣誉至少保留一个空条目，便于继续编辑） */
function mergeResume(base, incoming) {
  const src = incoming || {}
  const listOrEmpty = (key) => {
    const arr = Array.isArray(src[key]) ? src[key].map(pickItem) : []
    return arr.length ? arr : [emptyItem()]
  }
  const honors = Array.isArray(src.honors) ? src.honors.map(pickHonor) : []
  return {
    ...base,
    ...src,
    education: listOrEmpty('education'),
    work_experience: listOrEmpty('work_experience'),
    project_experience: listOrEmpty('project_experience'),
    campus_experience: listOrEmpty('campus_experience'),
    honors: honors.length ? honors : [emptyHonor()],
    skills: Array.isArray(src.skills) ? src.skills : base.skills || [],
  }
}

/** 分值分档：驱动 ATS 报告的配色类名 */
const bandOf = (score) => (score >= 80 ? 'good' : score >= 60 ? 'fair' : 'poor')

/** 证件照：本地按 5:7 居中裁剪并压到 300×420，避免 base64 过大 */
function cropPhoto(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = (e) => {
      const img = new Image()
      img.onload = () => {
        const srcRatio = img.width / img.height
        const dstRatio = PHOTO_W / PHOTO_H
        let sw = img.width
        let sh = img.height
        let sx = 0
        let sy = 0
        if (srcRatio > dstRatio) {
          sw = Math.round(img.height * dstRatio)
          sx = Math.round((img.width - sw) / 2)
        } else {
          sh = Math.round(img.width / dstRatio)
          sy = Math.round((img.height - sh) / 2)
        }
        const canvas = document.createElement('canvas')
        canvas.width = PHOTO_W
        canvas.height = PHOTO_H
        canvas.getContext('2d').drawImage(img, sx, sy, sw, sh, 0, 0, PHOTO_W, PHOTO_H)
        resolve(canvas.toDataURL('image/jpeg', 0.9))
      }
      img.onerror = () => reject(new Error('图片读取失败，请换一张试试'))
      img.src = e.target.result
    }
    reader.onerror = () => reject(new Error('图片读取失败，请换一张试试'))
    reader.readAsDataURL(file)
  })
}

/* ==========================================================================
   通用子组件
   ========================================================================== */

/** 带可见标签的表单行（label 直接包裹控件，天然完成 label↔input 关联） */
function Field({ label, hint, children, className = '' }) {
  return (
    <label className={`ro-field ${className}`}>
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="ro-hint">{hint}</span>}
    </label>
  )
}

/** 技能标签输入 */
function SkillInput({ value, onChange }) {
  const [text, setText] = useState('')
  const add = (raw) => {
    const v = String(raw || '').trim()
    if (!v || value.includes(v)) return
    onChange([...value, v])
    setText('')
  }
  return (
    <div>
      <div className="tag-input">
        <input
          className="input"
          value={text}
          placeholder="输入技能后回车添加，如：Python"
          aria-label="添加技能"
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault()
              add(text)
            }
          }}
        />
        <button type="button" className="btn btn--soft" onClick={() => add(text)}>
          添加
        </button>
      </div>
      {value.length > 0 && (
        <div className="tag-list mt-8">
          {value.map((t, i) => (
            <span className="tag-edit" key={`${t}-${i}`}>
              {t}
              <button
                type="button"
                aria-label={`删除技能 ${t}`}
                onClick={() => onChange(value.filter((_, idx) => idx !== i))}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="ro-chips mt-8">
        <span className="ro-chips-label">常用：</span>
        {SKILL_SUGGESTIONS.filter((s) => !value.includes(s)).map((s) => (
          <button type="button" className="chip ro-chip-btn" key={s} onClick={() => add(s)}>
            <IconPlus size={13} />
            {s}
          </button>
        ))}
      </div>
    </div>
  )
}

/** 证件照：预览 + 上传 + 清除 */
function PhotoField({ value, onChange }) {
  const [busy, setBusy] = useState(false)
  const onPick = async (file) => {
    if (!file) return
    if (!/^image\//.test(file.type || '')) {
      notify('证件照仅支持图片格式（JPG / PNG）', 'error')
      return
    }
    setBusy(true)
    try {
      onChange(await cropPhoto(file))
    } catch (err) {
      notify(err.message || '图片处理失败', 'error')
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="ro-photo-upload">
      <div className="ro-photo-preview">
        {value ? (
          <img src={value} alt="证件照预览" />
        ) : (
          <span className="ro-photo-placeholder">
            <IconPhoto size={20} />
            未上传
            <br />
            证件照
          </span>
        )}
      </div>
      <div className="ro-photo-actions">
        <label className="btn btn--ghost btn--sm ro-file-btn">
          {busy ? <span className="spinner" /> : <IconUpload size={16} />}
          {busy ? '处理中…' : value ? '更换证件照' : '上传证件照'}
          <input
            type="file"
            accept="image/*"
            hidden
            onChange={(e) => {
              onPick(e.target.files?.[0])
              e.target.value = ''
            }}
          />
        </label>
        {value && (
          <button type="button" className="btn btn--ghost btn--sm" onClick={() => onChange('')}>
            清除
          </button>
        )}
        <span className="ro-hint">会自动裁成一寸比例（5:7）；不传则用模板自带的占位图</span>
      </div>
    </div>
  )
}

/** 可增删的经历编辑器（教育 / 工作 / 项目 / 校园共用） */
function ExperienceEditor({ section, items, onChange }) {
  const list = items || []
  const patch = (idx, name, v) =>
    onChange(list.map((it, i) => (i === idx ? { ...it, [name]: v } : it)))
  const add = () => onChange([...list, emptyItem()])
  const remove = (idx) => onChange(list.filter((_, i) => i !== idx))

  return (
    <div>
      {list.length === 0 && (
        <div className="ro-empty">还没有内容，点下方按钮添加一条；留空不会出现在简历里</div>
      )}
      {list.map((it, idx) => (
        <div className={`ro-item ${section.tone}`} key={idx}>
          <div className="ro-item-head">
            <span className="ro-item-no">{section.title} {idx + 1}</span>
            <button
              type="button"
              className="btn btn--ghost btn--sm ro-item-del"
              onClick={() => remove(idx)}
              aria-label={`删除第 ${idx + 1} 条${section.title}`}
            >
              <IconTrash size={15} />
              删除
            </button>
          </div>
          <div className="ro-grid-4">
            {section.fields.map((f) => (
              <Field label={f.label} key={f.name}>
                <input
                  className="input"
                  value={it[f.name] || ''}
                  placeholder={f.placeholder}
                  onChange={(e) => patch(idx, f.name, e.target.value)}
                />
              </Field>
            ))}
            <Field label="开始时间">
              <input
                className="input"
                value={it.start_date || ''}
                placeholder="YYYY-MM"
                inputMode="numeric"
                onChange={(e) => patch(idx, 'start_date', e.target.value)}
              />
            </Field>
            <Field label="结束时间">
              <input
                className="input"
                value={it.end_date || ''}
                placeholder="YYYY-MM 或 至今"
                onChange={(e) => patch(idx, 'end_date', e.target.value)}
              />
            </Field>
          </div>
          <Field label="经历描述（用口语写就行）" className="mt-12">
            <textarea
              className="textarea"
              value={it.description || ''}
              placeholder={section.descPlaceholder}
              onChange={(e) => patch(idx, 'description', e.target.value)}
            />
          </Field>
        </div>
      ))}
      <button type="button" className="btn btn--soft btn--sm ro-add" onClick={add}>
        <IconPlus size={16} />
        添加{section.title}
      </button>
    </div>
  )
}

/** 荣誉奖项：时间 | 名称 | 等级（与 PDF 里三列顺序一致） */
function HonorEditor({ items, onChange }) {
  const list = items || []
  const patch = (idx, name, v) =>
    onChange(list.map((it, i) => (i === idx ? { ...it, [name]: v } : it)))
  const add = () => onChange([...list, emptyHonor()])
  const remove = (idx) => onChange(list.filter((_, i) => i !== idx))

  return (
    <div>
      {list.map((it, idx) => (
        <div className="ro-item tone-amber" key={idx}>
          <div className="ro-item-head">
            <span className="ro-item-no">荣誉 {idx + 1}</span>
            <button
              type="button"
              className="btn btn--ghost btn--sm ro-item-del"
              onClick={() => remove(idx)}
              aria-label={`删除第 ${idx + 1} 条荣誉`}
            >
              <IconTrash size={15} />
              删除
            </button>
          </div>
          <div className="ro-grid-3">
            <Field label="获奖时间">
              <input
                className="input"
                value={it.date || ''}
                placeholder="YYYY-MM"
                inputMode="numeric"
                onChange={(e) => patch(idx, 'date', e.target.value)}
              />
            </Field>
            <Field label="荣誉名称">
              <input
                className="input"
                value={it.name || ''}
                placeholder="如：国家奖学金"
                onChange={(e) => patch(idx, 'name', e.target.value)}
              />
            </Field>
            <Field label="奖励等级">
              <input
                className="input"
                value={it.level || ''}
                placeholder="如：国家级 / 校级一等奖"
                onChange={(e) => patch(idx, 'level', e.target.value)}
              />
            </Field>
          </div>
        </div>
      ))}
      <button type="button" className="btn btn--soft btn--sm ro-add" onClick={add}>
        <IconPlus size={16} />
        添加荣誉奖项
      </button>
    </div>
  )
}

/** ATS 单个模块卡片 */
function AtsModule({ mod }) {
  const band = bandOf(mod.score)
  return (
    <div className={`ro-module ro-band-${band}`}>
      <div className="ro-module-head">
        <span className="ro-module-name">{mod.name}</span>
        <span className="chip ro-module-level">{mod.level}</span>
        <span className="ro-module-score mono">{mod.score}/100</span>
      </div>
      {mod.description && <p className="ro-module-desc">{mod.description}</p>}
      <div className="ro-bar" role="img" aria-label={`${mod.name} 得分 ${mod.score} 分`}>
        <i style={{ width: `${Math.max(0, Math.min(100, mod.score))}%` }} />
      </div>
      {mod.summary && <p className="ro-module-summary">{mod.summary}</p>}
      {[
        ['issues', '发现的问题', 'ro-list--issue'],
        ['suggestions', '修改建议', 'ro-list--tip'],
        ['highlights', '做得好的地方', 'ro-list--ok'],
      ].map(([key, title, cls]) =>
        (mod[key] || []).length ? (
          <div key={key}>
            <div className="ro-list-title">{title}</div>
            <ul className={`ro-list ${cls}`}>
              {mod[key].map((t, i) => (
                <li key={i}>{t}</li>
              ))}
            </ul>
          </div>
        ) : null
      )}
    </div>
  )
}

/* ==========================================================================
   页面
   ========================================================================== */
export default function ResumeOptimize() {
  // 登录态：简历优化会往服务端写数据（解析结果、PDF），游客模式下不提供
  const { isGuest, loading: authLoading } = useAuth()
  const [meta, setMeta] = useState(null)
  const [resume, setResume] = useState(() => blankResume())
  const [jd, setJd] = useState('')
  const [language, setLanguage] = useState('zh')

  const [parsing, setParsing] = useState(false)
  const [polishing, setPolishing] = useState(false)
  const [atsLoading, setAtsLoading] = useState(false)
  const [downloading, setDownloading] = useState('')
  const [ats, setAts] = useState(null)
  const [undoSnapshot, setUndoSnapshot] = useState(null)
  const [parseWarnings, setParseWarnings] = useState([])
  /* ---- 体验增强 §11：JD 关键词覆盖率 + 润色改动对照 ---- */
  const [coverage, setCoverage] = useState(null)
  const [polishDiff, setPolishDiff] = useState(null)
  const [revertedGroups, setRevertedGroups] = useState({})

  const atsRef = useRef(null)

  useEffect(() => {
    getResumeOptimizerMeta()
      .then((data) => {
        setMeta(data)
        setResume((prev) => blankResume(data?.empty_resume || prev))
      })
      .catch(() => setMeta(null))
  }, [])

  const llmReady = meta ? meta.llm_mode === 'live' : true
  const pdfReady = meta ? meta.pdf_available !== false : true
  const maxMb = meta?.max_upload_mb || 5
  const templates = useMemo(() => meta?.templates || [], [meta])

  const setField = useCallback((name, value) => setResume((r) => ({ ...r, [name]: value })), [])
  const setList = useCallback((key, value) => setResume((r) => ({ ...r, [key]: value })), [])

  /* ---- 上传旧简历 PDF ---- */
  const onUpload = async (file) => {
    if (!file) return
    if (!/\.pdf$/i.test(file.name || '')) {
      notify('仅支持 PDF 文件（.pdf），请重新选择', 'error')
      return
    }
    if (file.size > maxMb * 1024 * 1024) {
      notify(`文件 ${(file.size / 1024 / 1024).toFixed(1)}MB 超过上限 ${maxMb}MB`, 'error')
      return
    }
    if (!llmReady) {
      notify('当前后端未配置大模型 Key，无法自动解析简历，请手动填写', 'warn', 3600)
      return
    }
    setParsing(true)
    setParseWarnings([])
    try {
      const data = await uploadResumeForOptimize(file)
      // 解析结果一律保留「操作前版本」的可撤销性
      setUndoSnapshot(resume)
      // 证件照不参与解析：用户已经上传的那张要保留下来
      setResume(mergeResume(blankResume(meta?.empty_resume), { ...(data?.resume || {}), photo: resume.photo || '' }))
      setParseWarnings(data?.warnings || [])
      notify('简历解析完成，请核对下方内容后按需修改', 'success')
    } catch {
      /* client 已弹 Toast */
    } finally {
      setParsing(false)
    }
  }

  /* ---- 润色 ---- */
  const onPolish = async () => {
    if (!llmReady) {
      notify('当前后端未配置大模型 Key，润色功能不可用', 'warn', 3600)
      return
    }
    if (!hasContent(resume)) {
      notify('请先填写至少一段经历或基本信息，再让 AI 润色', 'warn')
      return
    }
    setPolishing(true)
    try {
      const data = await polishResume(resume, jd, language)
      setUndoSnapshot(resume)
      // 体验增强 §11：留一份润色前的快照用于改动对照（可逐条还原本地修改）
      setPolishDiff({ before: resume, after: mergeResume(resume, data) })
      setRevertedGroups({})
      // 润色结果直接回填表单：用户能看见 AI 改了什么，也能继续手动微调
      setResume((prev) => mergeResume(prev, { ...data, photo: prev.photo || '' }))
      notify(
        language === 'en' ? '润色完成（英文版），已回填到表单' : '润色完成，已按 STAR 三段式回填到表单',
        'success',
        3600
      )
    } catch {
      /* client 已弹 Toast */
    } finally {
      setPolishing(false)
    }
  }

  /* ---- ATS 检测 ---- */
  const onCheckAts = async () => {
    if (!llmReady) {
      notify('当前后端未配置大模型 Key，ATS 检测不可用', 'warn', 3600)
      return
    }
    if (!hasContent(resume)) {
      notify('请先填写简历内容，再做 ATS 检测', 'warn')
      return
    }
    setAtsLoading(true)
    try {
      const data = await checkResumeAts(resume, jd)
      setAts(data)
      notify(`ATS 检测完成：${data.score} 分（等级 ${data.grade}）`, 'success')
      // 面板在下方，滚动过去；等一帧确保 DOM 已渲染
      setTimeout(() => atsRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' }), 60)
    } catch {
      /* client 已弹 Toast */
    } finally {
      setAtsLoading(false)
    }
  }

  /* ---- 下载 PDF ---- */
  const onDownload = async (templateId) => {
    if (!pdfReady) {
      notify('后端未检测到 wkhtmltopdf，PDF 导出不可用', 'warn', 3600)
      return
    }
    if (!hasContent(resume)) {
      notify('简历内容为空，请先填写或上传旧简历', 'warn')
      return
    }
    setDownloading(templateId)
    try {
      const { blob, pages, filename } = await renderResumePdf(resume, templateId, language)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = filename
      document.body.appendChild(a)
      a.click()
      a.remove()
      // 交给浏览器读完再释放，立刻 revoke 在部分浏览器会中断下载
      setTimeout(() => URL.revokeObjectURL(url), 4000)
      if (pages > 1) {
        notify(`内容偏多：已缩到最小字号仍排出 ${pages} 页，建议精简经历或描述字数`, 'warn', 5200)
      } else {
        notify('PDF 已生成，请在下载目录查看', 'success')
      }
    } catch {
      /* client 已弹 Toast */
    } finally {
      setDownloading('')
    }
  }

  const onRestore = () => {
    if (!undoSnapshot) return
    setResume(undoSnapshot)
    setUndoSnapshot(null)
    notify('已还原到上一次操作前的内容', 'info')
  }

  /* ==========================================================================
     体验增强 §11：JD 关键词覆盖率看板 + 润色改动对照
     ========================================================================== */

  /** 覆盖率自动重算：规则接口、无 LLM 费用，因此可以用防抖随输入刷新 */
  useEffect(() => {
    if (!jd.trim()) {
      setCoverage(null)
      return undefined
    }
    let alive = true
    const t = setTimeout(() => {
      getKeywordCoverage(resume, jd)
        .then((res) => {
          if (alive) setCoverage(res)
        })
        .catch(() => {
          if (alive) setCoverage(null)
        })
    }, 600)
    return () => {
      alive = false
      clearTimeout(t)
    }
  }, [resume, jd])

  /** 「加入我的简历」：只对技能类缺失词开放，插进技能标签后由用户再编辑 */
  const addSkillKeyword = (word) => {
    const current = resume.skills || []
    if (current.includes(word)) return
    setList('skills', [...current, word])
    notify(`已把「${word}」加入技能特长，可继续编辑或删除`, 'success', 3000)
  }

  /**
   * 改动对照（体验增强 §11）。
   *
   * 后端返回的是**结构化字段**而不是句子级 diff，因此对照的单位是"字段/条目"：
   * 每条改动的经历、每个变化的字段各成一行，可单独还原。
   *
   * 说明：按条还原而不是"逐句接受/回退"——逐句需要引入真正的 diff 引擎并定义
   * 句子对齐规则，复杂度高而收益有限；用户真正需要的是"看得见改了什么 + 能撤回"，
   * 这两点由本模块满足。
   */
  const diffGroups = useMemo(() => {
    if (!polishDiff) return []
    const { before, after } = polishDiff
    const groups = []
    const push = (key, label, b, a) => {
      const bv = String(b || '').trim()
      const av = String(a || '').trim()
      if (bv === av) return
      groups.push({ key, label, before: bv, after: av })
    }

    push('job_intention', '求职意向', before?.basic?.job_intention, after?.basic?.job_intention)
    push('skills', '技能特长', (before?.skills || []).join('、'), (after?.skills || []).join('、'))
    for (const sec of EXPERIENCE_SECTIONS) {
      const bList = before?.[sec.key] || []
      const aList = after?.[sec.key] || []
      for (let i = 0; i < Math.max(bList.length, aList.length); i += 1) {
        const b = bList[i] || {}
        const a = aList[i] || {}
        const name = a.name || a.company || b.name || b.company || `第 ${i + 1} 条`
        push(`${sec.key}.${i}.description`, `${sec.title} · ${name}`, b.description, a.description)
      }
    }
    return groups
  }, [polishDiff])

  /** 还原某一组到润色前的内容（其余组保持润色结果） */
  const revertGroup = (groupKey) => {
    if (!polishDiff) return
    const { before } = polishDiff
    if (groupKey === 'job_intention') {
      setResume((r) => ({ ...r, job_intention: before?.basic?.job_intention || '' }))
    } else if (groupKey === 'skills') {
      setResume((r) => ({ ...r, skills: [...(before?.skills || [])] }))
    } else {
      const [listKey, idxRaw] = groupKey.split('.')
      const idx = Number(idxRaw)
      setResume((r) => ({
        ...r,
        [listKey]: (r[listKey] || []).map((it, i) =>
          i === idx ? { ...it, description: before?.[listKey]?.[idx]?.description || '' } : it
        ),
      }))
    }
    setRevertedGroups((prev) => ({ ...prev, [groupKey]: true }))
  }

  // 游客模式：简历优化不提供（游客的数据一律不保存，这里也会写服务端）
  if (!authLoading && isGuest) {
    return (
      <LoginRequired
        what="简历优化"
        points={[
          'STAR 润色：把流水账改写成有结果的表达',
          'ATS 检测：五模块诊断 + 关键词覆盖率',
          '一键导出 PDF 简历',
        ]}
      />
    )
  }

  return (
    <div className="container">
      <div className="page-head">
        <div>
          <h1 className="page-title">简历优化</h1>
          <p className="page-sub">
            口语化的经历也能变成专业简历：AI 按 STAR 法则润色，并给出 ATS 兼容性诊断与一键导出 PDF
          </p>
        </div>
        <div className="row gap-8">
          <button className="btn btn--ghost btn--sm" onClick={() => setResume(demoResume(meta?.empty_resume))}>
            <IconSparkle size={16} />
            填入示例
          </button>
          <button className="btn btn--ghost btn--sm" onClick={() => setResume(blankResume(meta?.empty_resume))}>
            清空表单
          </button>
        </div>
      </div>

      {/* ---------------- 环境提示 ---------------- */}
      {meta && !llmReady && (
        <div className="alert alert--warn mb-12" role="status">
          <IconWarn size={17} />
          <span>
            后端当前是<strong>演示模式</strong>（未配置大模型 Key），润色与 ATS 检测不可用。
            请在 <span className="mono">backend/.env</span> 中填写 DEEPSEEK_API_KEY / ZHIPU_API_KEY 之一后重启后端；
            表单填写与 PDF 导出不受影响。
          </span>
        </div>
      )}
      {meta && !pdfReady && (
        <div className="alert alert--warn mb-12" role="status">
          <IconWarn size={17} />
          <span>
            未检测到 <span className="mono">wkhtmltopdf</span>，「下载 PDF」暂不可用；
            安装后重启后端即可（或设置环境变量 WKHTMLTOPDF_PATH）。
          </span>
        </div>
      )}

      {/* ---------------- 上传旧简历 ---------------- */}
      <SectionCard
        index="①"
        title="导入旧简历"
        desc={`上传 PDF 简历自动解析并回填下方表单（≤ ${maxMb}MB）；也可以直接在下面手动填写`}
        right={parsing ? <span className="chip chip--primary">解析中…</span> : <span className="chip">可选</span>}
      >
        <label
          className="dropzone"
          onDragOver={(e) => {
            e.preventDefault()
            e.currentTarget.classList.add('over')
          }}
          onDragLeave={(e) => e.currentTarget.classList.remove('over')}
          onDrop={(e) => {
            e.preventDefault()
            e.currentTarget.classList.remove('over')
            onUpload(e.dataTransfer?.files?.[0])
          }}
        >
          <input
            type="file"
            accept="application/pdf,.pdf"
            hidden
            onChange={(e) => {
              onUpload(e.target.files?.[0])
              e.target.value = ''
            }}
          />
          {parsing ? (
            <span className="spinner spinner--primary" />
          ) : (
            <IconUpload size={26} style={{ color: 'var(--text-mute)' }} />
          )}
          <div className="dropzone-title">
            {parsing ? '正在解析你的旧简历…' : '拖拽 PDF 简历到此处，或点击选择文件'}
          </div>
          <div className="dropzone-sub">
            {parsing ? '大模型正在提取教育 / 工作 / 项目 / 荣誉等信息，通常需要 30-90 秒' : '仅支持 PDF 格式；解析后可在下方逐项修改'}
          </div>
        </label>
        {parseWarnings.length > 0 && (
          <div className="alert alert--warn mt-12">
            <IconWarn size={17} />
            <span>{parseWarnings.join('；')}</span>
          </div>
        )}
        {undoSnapshot && (
          <div className="alert mt-12">
            <IconInfo size={17} />
            <span className="grow">内容被 AI 或解析结果覆盖了？可以一键还原到操作前的版本。</span>
            <button className="link-btn" onClick={onRestore}>
              还原
            </button>
          </div>
        )}
      </SectionCard>

      {/* ---------------- 基本信息 ---------------- */}
      <SectionCard index="②" title="基本信息" desc="两个模板的顶部信息区都会用到；求职意向与标语会直接影响观感">
        <div className="form-grid form-grid--3">
          <Field label="姓名">
            <input
              className="input"
              value={resume.name || ''}
              placeholder="如：林宇航"
              autoComplete="name"
              onChange={(e) => setField('name', e.target.value)}
            />
          </Field>
          <Field label="求职意向">
            <input
              className="input"
              value={resume.job_intention || ''}
              placeholder="如：后端开发工程师"
              onChange={(e) => setField('job_intention', e.target.value)}
            />
          </Field>
          <Field label="电话">
            <input
              className="input"
              type="tel"
              inputMode="tel"
              value={resume.phone || ''}
              placeholder="如：13800138000"
              autoComplete="tel"
              onChange={(e) => setField('phone', e.target.value)}
            />
          </Field>
          <Field label="邮箱">
            <input
              className="input"
              type="email"
              inputMode="email"
              value={resume.email || ''}
              placeholder="如：linyh@example.com"
              autoComplete="email"
              onChange={(e) => setField('email', e.target.value)}
            />
          </Field>
          <Field label="年龄">
            <input
              className="input"
              inputMode="numeric"
              value={resume.age || ''}
              placeholder="如：22"
              onChange={(e) => setField('age', e.target.value)}
            />
          </Field>
          <Field label="性别">
            <input
              className="input"
              value={resume.gender || ''}
              placeholder="如：男"
              onChange={(e) => setField('gender', e.target.value)}
            />
          </Field>
          <Field label="民族">
            <input
              className="input"
              value={resume.ethnicity || ''}
              placeholder="如：汉"
              onChange={(e) => setField('ethnicity', e.target.value)}
            />
          </Field>
          <Field label="微信">
            <input
              className="input"
              value={resume.wechat || ''}
              placeholder="如：linyh_wx"
              onChange={(e) => setField('wechat', e.target.value)}
            />
          </Field>
          <Field label="政治面貌">
            <input
              className="input"
              value={resume.political_status || ''}
              placeholder="如：共青团员"
              onChange={(e) => setField('political_status', e.target.value)}
            />
          </Field>
        </div>
        <div className="mt-12">
          <Field label="顶部标语（模板 1 的标题区）" hint="一句话概括你的定位，长度控制在 20 字以内最好看">
            <input
              className="input"
              value={resume.slogan || ''}
              placeholder="如：专注后端开发 · 追求极致的工程效率"
              onChange={(e) => setField('slogan', e.target.value)}
            />
          </Field>
        </div>
        <PhotoField value={resume.photo || ''} onChange={(v) => setField('photo', v)} />
      </SectionCard>

      {/* ---------------- 四类经历 ---------------- */}
      {EXPERIENCE_SECTIONS.map((section) => (
        <SectionCard
          key={section.key}
          index={section.index}
          title={section.title}
          desc={section.hint}
          right={
            <span className="chip">
              {(resume[section.key] || []).length} 条
            </span>
          }
        >
          <ExperienceEditor
            section={section}
            items={resume[section.key]}
            onChange={(v) => setList(section.key, v)}
          />
        </SectionCard>
      ))}

      {/* ---------------- 荣誉奖项 ---------------- */}
      <SectionCard
        index="⑦"
        title="荣誉奖项"
        desc="每条独立成段：获奖时间 | 荣誉名称 | 奖励等级，PDF 里据此三列对齐"
        right={<span className="chip">{(resume.honors || []).length} 条</span>}
      >
        <HonorEditor items={resume.honors} onChange={(v) => setList('honors', v)} />
      </SectionCard>

      {/* ---------------- 技能 ---------------- */}
      <SectionCard index="⑧" title="技能特长" desc="以标签形式渲染在简历末尾，建议 5-10 项，突出与岗位相关的硬技能">
        <SkillInput value={resume.skills || []} onChange={(v) => setList('skills', v)} />
      </SectionCard>

      {/* ---------------- JD ---------------- */}
      <SectionCard
        index="⑨"
        title="目标岗位 JD"
        desc="粘贴岗位描述后，润色会主动贴合 JD 关键词，ATS 检测也会据此判断关键词匹配度"
        right={jd.trim() ? <span className="chip chip--ok">已填写</span> : <span className="chip">可选</span>}
      >
        <Field label="岗位描述（JD）">
          <textarea
            className="textarea"
            rows={5}
            value={jd}
            placeholder="把招聘网站上的岗位职责与任职要求整段粘贴到这里…"
            onChange={(e) => setJd(e.target.value)}
          />
        </Field>

        {/* ================= JD 关键词覆盖率看板（体验增强 §11）=================
            纯规则计算、不调用大模型：改完简历立刻能看到覆盖率变化，
            零密钥环境同样可用。 */}
        {jd.trim() && (
          <div className="ro-coverage">
            <div className="row row--between row--wrap gap-8">
              <span className="block-label" style={{ margin: 0 }}>
                <IconScanCheck size={15} className="inline-ic" /> JD 关键词覆盖率
              </span>
              {coverage ? (
                <span
                  className={`chip mono ${
                    coverage.total > 0 && coverage.covered_count >= coverage.total
                      ? 'chip--ok'
                      : 'chip--warn'
                  }`}
                >
                  已覆盖 {coverage.covered_count} / 目标 {coverage.total}
                </span>
              ) : (
                <span className="chip">计算中…</span>
              )}
            </div>

            {coverage && coverage.total > 0 ? (
              <>
                {(coverage.covered || []).length > 0 && (
                  <div className="ro-kw-line">
                    <span className="mute-2">已命中</span>
                    {coverage.covered.map((w) => (
                      <span className="chip chip--ok" key={w}>
                        {w}
                      </span>
                    ))}
                  </div>
                )}
                {(coverage.missing || []).length > 0 && (
                  <div className="ro-kw-line">
                    <span className="mute-2">缺失</span>
                    {coverage.missing.map((w) => (
                      <span className="chip chip--warn ro-kw-miss" key={w}>
                        {w}
                        {(coverage.skill_missing || []).includes(w) && (
                          <button
                            type="button"
                            className="ro-kw-add"
                            title="加入技能特长（可再编辑）"
                            onClick={() => addSkillKeyword(w)}
                          >
                            + 加入技能特长
                          </button>
                        )}
                      </span>
                    ))}
                  </div>
                )}
                {coverage.note && <p className="mute-2 ro-coverage-note">{coverage.note}</p>}
              </>
            ) : (
              coverage && <p className="mute-2 ro-coverage-note">{coverage.note}</p>
            )}
          </div>
        )}
      </SectionCard>

      {/* ---------------- 润色改动对照（体验增强 §11）----------------
          AI 润色必须可撤销，否则用户不敢用：这里把每处改动摊开，
          可逐条还原，也能一键全部撤回。 */}
      {polishDiff && diffGroups.length > 0 && (
        <SectionCard
          index="⑩"
          title="润色改动对照"
          desc="左为润色前、右为润色后；不满意的地方可以单独还原，改动已被回填到上方表单"
          right={
            <div className="row gap-8">
              <span className="chip">{diffGroups.length} 处改动</span>
              <button
                className="btn btn--ghost btn--sm"
                onClick={() => {
                  setResume(polishDiff.before)
                  setPolishDiff(null)
                  setRevertedGroups({})
                  notify('已全部还原到润色前的内容', 'info')
                }}
              >
                全部还原
              </button>
            </div>
          }
        >
          <div className="ro-diff-list">
            {diffGroups.map((g) => (
              <div className={`ro-diff ${revertedGroups[g.key] ? 'is-reverted' : ''}`} key={g.key}>
                <div className="ro-diff-head">
                  <span className="ro-diff-label">{g.label}</span>
                  {revertedGroups[g.key] ? (
                    <span className="chip chip--ok">已还原</span>
                  ) : (
                    <button className="btn btn--ghost btn--sm" onClick={() => revertGroup(g.key)}>
                      还原这段
                    </button>
                  )}
                </div>
                <div className="ro-diff-cols">
                  <div className="ro-diff-col">
                    <div className="ro-diff-col-k">润色前</div>
                    <p>{g.before || '（空）'}</p>
                  </div>
                  <div className="ro-diff-col ro-diff-col--after">
                    <div className="ro-diff-col-k">润色后</div>
                    <p>{g.after || '（空）'}</p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </SectionCard>
      )}

      {/* ---------------- 底部操作栏 ---------------- */}
      <div className="ro-actionbar">
        <div className="ro-toolbar">
          <div className="seg" role="group" aria-label="简历语言">
            <button
              className={language === 'zh' ? 'on' : ''}
              aria-pressed={language === 'zh'}
              onClick={() => setLanguage('zh')}
            >
              中文
            </button>
            <button
              className={language === 'en' ? 'on' : ''}
              aria-pressed={language === 'en'}
              onClick={() => setLanguage('en')}
            >
              English
            </button>
          </div>
          <button className="btn btn--primary" onClick={onPolish} disabled={polishing || !llmReady}>
            {polishing ? <span className="spinner" /> : <IconWand size={17} />}
            {polishing ? '润色中…' : 'AI 润色简历'}
          </button>
          <button className="btn btn--ghost" onClick={onCheckAts} disabled={atsLoading || !llmReady}>
            {atsLoading ? <span className="spinner" /> : <IconScanCheck size={17} />}
            {atsLoading ? '检测中…' : 'ATS 检测'}
          </button>
          <div className="ro-downloads">
            {templates.map((t, i) => (
              <button
                key={t.id}
                className={`btn btn--ghost ${i === 0 ? 'ro-dl-1' : 'ro-dl-2'}`}
                onClick={() => onDownload(t.id)}
                disabled={!!downloading || !pdfReady}
                title={t.desc}
              >
                {downloading === t.id ? <span className="spinner" /> : <IconDownload size={17} />}
                {downloading === t.id ? '生成中…' : `下载 PDF · ${t.name}`}
              </button>
            ))}
          </div>
        </div>
        {polishing && (
          <div className="ro-progress" role="status">
            <span className="spinner spinner--primary" />
            正在调用大模型润色并重构 STAR 段落，通常需要 30-90 秒，请不要关闭页面
          </div>
        )}
        {atsLoading && (
          <div className="ro-progress" role="status">
            <span className="spinner spinner--primary" />
            正在按五个模块诊断 ATS 兼容性，通常需要 30-60 秒
          </div>
        )}
      </div>

      {/* ---------------- ATS 报告 ---------------- */}
      {ats && (
        <div className="card ro-ats fade-up mt-16" ref={atsRef}>
          <div className="card-head">
            <div>
              <div className="card-title">
                <IconScanCheck size={18} />
                ATS 兼容性检测报告
              </div>
              <div className="sec-desc">按五个模块逐项评分，问题均引用你简历或 JD 中的具体内容</div>
            </div>
            <button className="btn btn--ghost btn--sm" onClick={() => setAts(null)}>
              收起
            </button>
          </div>

          <div className="ro-ats-top">
            <ScoreRing
              value={ats.score}
              size={132}
              label="ATS 兼容度"
              center={
                <>
                  <div className="ro-ats-score mono">
                    {ats.score}
                    <span>/100</span>
                  </div>
                  <div className={`ro-ats-grade ro-band-${bandOf(ats.score)}`}>等级 {ats.grade}</div>
                </>
              }
            />
            <div className="grow">
              <div className="row row--wrap gap-8">
                <span className={`chip ${ats.score >= 80 ? 'chip--ok' : ats.score >= 60 ? 'chip--warn' : 'chip--danger'}`}>
                  {ats.level}
                </span>
                <span className="chip">总分 {ats.score}/100</span>
                <span className="chip">等级 {ats.grade}</span>
              </div>
              <p className={`ro-ats-summary ro-band-${bandOf(ats.score)}`}>
                {ats.summary || '暂无总体结论'}
              </p>
            </div>
          </div>

          <div className="ro-modules">
            {(ats.modules || []).map((m) => (
              <AtsModule mod={m} key={m.key} />
            ))}
          </div>

          <div className="ro-list-title">
            <IconSparkle size={14} className="inline-ic" />
            总体改进建议（按优先级）
          </div>
          {(ats.suggestions || []).length ? (
            <ul className="ro-suggestions">
              {ats.suggestions.map((s, i) => (
                <li key={i}>{s}</li>
              ))}
            </ul>
          ) : (
            <div className="alert alert--ok">
              <IconCheckCircle size={17} />
              <span>简历质量优秀，暂无改进建议。</span>
            </div>
          )}
        </div>
      )}

      <footer className="ro-foot">
        <span className="mute-2" style={{ fontSize: 12 }}>
          简历优化由 AI 生成，导出前请自行核对联系方式、时间与量化数据的真实性
        </span>
      </footer>
    </div>
  )
}
