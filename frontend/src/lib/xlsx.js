/**
 * 零依赖表格解析 / 生成（ES 模块 · 读写 .xlsx / .csv）
 *
 * 为什么不用 SheetJS：这个功能只需要「读 xlsx / 写 xlsx」，而 xlsx 本身就是
 * 一个 ZIP + 几个 XML。项目里引入一个几百 KB 的解析库，只为在导入向导里读一张表，
 * 性价比太低；自建 ZIP 读写用浏览器原生的 `DecompressionStream` / `CompressionStream`，
 * 零依赖、体积可忽略。整套读写都留在浏览器里，服务端只收 JSON。
 *
 * 能力与边界（包括那几句提示文案）：
 *   · .xlsx  自建 ZIP 读取 + DOMParser 解析 XML，支持多工作表、共享字符串、日期格式；
 *   · .csv / .txt / .tsv  自动识别分隔符（`,` `;` Tab `|`），支持引号包裹与 BOM；
 *   · 粘贴板 直接解析从 Excel 复制的 TSV；
 *   · ❌ 不支持旧版 .xls（二进制 OLE）与加密工作簿 —— 会给出「另存为」的可执行提示。
 *
 * 写出的 .xlsx 自带表头加粗、首行冻结、自动筛选、**真正的日期单元格**
 * （Excel 里可直接排序 / 做日期计算，不是文本），列宽按内容设置。
 */

/* ============================ 通用工具 ============================ */

const utf8 = new TextDecoder('utf-8')

function localName(el) {
  return el.localName || el.nodeName.replace(/^.*:/, '')
}

/** 取所有指定局部名的后代元素（对命名空间前缀不敏感） */
function tags(root, name) {
  const list = root.getElementsByTagNameNS
    ? root.getElementsByTagNameNS('*', name)
    : root.getElementsByTagName(name)
  return Array.prototype.slice.call(list || [])
}

function parseXml(text) {
  const doc = new DOMParser().parseFromString(text, 'application/xml')
  const err = doc.getElementsByTagName('parsererror')[0]
  if (err) throw new Error(`XML 解析失败：${err.textContent.slice(0, 120)}`)
  return doc
}

function pad2(n) {
  return String(n).padStart(2, '0')
}

/* ============================ ZIP 读取 ============================ */

const SIG_EOCD = 0x06054b50
const SIG_CD = 0x02014b50
const SIG_LFH = 0x04034b50

function buildZipIndex(buffer) {
  const view = new DataView(buffer)
  const len = buffer.byteLength

  /* 从尾部向前找 EOCD（End of Central Directory）；注释字段最长 65535 字节 */
  let eocd = -1
  const minPos = Math.max(0, len - 65557)
  for (let i = len - 22; i >= minPos; i--) {
    if (view.getUint32(i, true) === SIG_EOCD) {
      eocd = i
      break
    }
  }
  if (eocd < 0) {
    /* 给出可诊断的提示：旧版 .xls 的签名与 ZIP 完全不同，能识别就直说 */
    if (len >= 4 && view.getUint32(0, true) === 0xe011cfd0) {
      throw new Error(
        '这是旧版 .xls（或加密的 .xlsx）文件，无法直接解析。请在 Excel 中「另存为」为 .xlsx 或「CSV UTF-8」后重试。'
      )
    }
    throw new Error('不是有效的 .xlsx 文件（未找到 ZIP 结构）')
  }

  const count = view.getUint16(eocd + 10, true)
  const cdOffset = view.getUint32(eocd + 16, true)
  if (cdOffset === 0xffffffff) throw new Error('不支持 ZIP64 格式的超大文件，请另存为 CSV 后导入。')

  const index = {}
  let p = cdOffset
  for (let n = 0; n < count; n++) {
    if (p + 46 > len || view.getUint32(p, true) !== SIG_CD) break
    const method = view.getUint16(p + 10, true)
    const compSize = view.getUint32(p + 20, true)
    const nameLen = view.getUint16(p + 28, true)
    const extraLen = view.getUint16(p + 30, true)
    const commentLen = view.getUint16(p + 32, true)
    const localOffset = view.getUint32(p + 42, true)
    const name = utf8.decode(new Uint8Array(buffer, p + 46, nameLen))
    index[name] = { method, compSize, localOffset }
    p += 46 + nameLen + extraLen + commentLen
  }
  return { index, buffer }
}

/** 解压并解码一个 ZIP 条目为字符串 */
async function readEntry(zip, name) {
  const e = zip.index[name]
  if (!e) return null

  const buffer = zip.buffer
  const view = new DataView(buffer)
  const p = e.localOffset
  if (view.getUint32(p, true) !== SIG_LFH) throw new Error(`ZIP 局部头损坏：${name}`)

  const nameLen = view.getUint16(p + 26, true)
  const extraLen = view.getUint16(p + 28, true)
  const start = p + 30 + nameLen + extraLen

  /* 局部头里的压缩大小更可靠；central directory 为 0 时回退 */
  let compSize = view.getUint32(p + 18, true) || e.compSize
  if (!compSize) compSize = buffer.byteLength - start

  const data = new Uint8Array(buffer, start, Math.min(compSize, buffer.byteLength - start))

  if (e.method === 0) return utf8.decode(data)
  if (e.method !== 8) throw new Error(`不支持的 ZIP 压缩方式（method=${e.method}）`)

  if (typeof DecompressionStream !== 'function') {
    throw new Error('当前浏览器不支持解压 .xlsx，请改用 CSV 导入（Excel → 另存为 → CSV UTF-8）。')
  }
  try {
    const stream = new Blob([data]).stream().pipeThrough(new DecompressionStream('deflate-raw'))
    const out = await new Response(stream).arrayBuffer()
    return utf8.decode(new Uint8Array(out))
  } catch (err) {
    throw new Error(`解压 ${name} 失败：${err?.message ? err.message : err}`)
  }
}

/* ============================ xlsx 内部结构解析 ============================ */

/** 共享字符串表 */
function parseSharedStrings(xml) {
  if (!xml) return []
  const doc = parseXml(xml)
  return tags(doc, 'si').map((si) => {
    /* 排除注音（rPh）里的 t，避免拼音混进正文 */
    const ts = tags(si, 't').filter((t) => {
      const par = t.parentNode
      return !(par && localName(par) === 'rPh')
    })
    if (!ts.length) return ''
    return ts.map((t) => t.textContent).join('')
  })
}

/** 样式表 → 每个 cellXfs 索引是否日期格式 */
function parseStyles(xml) {
  const isDate = []
  if (!xml) return isDate
  let doc
  try {
    doc = parseXml(xml)
  } catch {
    return isDate
  }

  const custom = {}
  tags(doc, 'numFmt').forEach((nf) => {
    const id = parseInt(nf.getAttribute('numFmtId'), 10)
    if (Number.isFinite(id)) custom[id] = nf.getAttribute('formatCode') || ''
  })

  const BUILTIN_DATE = {
    14: 1, 15: 1, 16: 1, 17: 1, 18: 1, 19: 1, 20: 1, 21: 1, 22: 1, 45: 1, 46: 1, 47: 1,
  }

  const looksLikeDate = (fmt) => {
    if (!fmt) return false
    const cleaned = String(fmt).replace(/"[^"]*"/g, '').replace(/\[[^\]]*\]/g, '')
    return /[ymdh]/i.test(cleaned)
  }

  /* 只取 cellXfs 下的 xf —— cellStyleXfs 里也有 xf，混在一起会让样式索引错位 */
  const cellXfs = tags(doc, 'cellXfs')[0]
  if (!cellXfs) return isDate

  tags(cellXfs, 'xf').forEach((xf) => {
    const id = parseInt(xf.getAttribute('numFmtId'), 10)
    if (!Number.isFinite(id)) {
      isDate.push(false)
      return
    }
    isDate.push(!!BUILTIN_DATE[id] || looksLikeDate(custom[id]))
  })
  return isDate
}

function colIndexFromRef(ref) {
  const m = /^([A-Za-z]+)/.exec(ref || '')
  if (!m) return -1
  const s = m[1].toUpperCase()
  let n = 0
  for (let i = 0; i < s.length; i++) n = n * 26 + (s.charCodeAt(i) - 64)
  return n - 1
}

function serialToISO(n, date1904) {
  const epochOffset = date1904 ? 24107 : 25569 /* 距 1970-01-01 的天数 */
  const ms = Math.round((n - epochOffset) * 86400000)
  const d = new Date(ms)
  if (Number.isNaN(d.getTime())) return ''
  return `${d.getUTCFullYear()}-${pad2(d.getUTCMonth() + 1)}-${pad2(d.getUTCDate())}`
}

function cellText(c, shared, dateStyles, date1904) {
  const t = c.getAttribute('t')

  if (t === 'inlineStr') {
    const is = tags(c, 'is')[0]
    if (!is) return ''
    return tags(is, 't')
      .map((x) => x.textContent)
      .join('')
  }

  const v = tags(c, 'v')[0]
  if (!v) return ''
  const raw = v.textContent == null ? '' : v.textContent

  if (t === 's') {
    const idx = parseInt(raw, 10)
    return Number.isFinite(idx) && shared[idx] != null ? shared[idx] : ''
  }
  if (t === 'str') return raw
  if (t === 'b') return raw === '1' ? '是' : '否'
  if (t === 'e') return ''

  /* 数值：按样式判断是否日期 */
  const n = Number(raw)
  if (!Number.isFinite(n)) return raw
  const s = c.getAttribute('s')
  if (s != null && dateStyles[parseInt(s, 10)]) return serialToISO(n, date1904)
  return raw
}

function parseSheet(xml, shared, dateStyles, date1904) {
  const doc = parseXml(xml)
  const rowEls = tags(doc, 'row')
  const rows = []

  rowEls.forEach((rowEl) => {
    const cells = tags(rowEl, 'c')
    if (!cells.length) return
    const arr = []
    let maxCol = -1

    cells.forEach((c) => {
      let ci = colIndexFromRef(c.getAttribute('r'))
      if (ci < 0) ci = arr.length
      arr[ci] = cellText(c, shared, dateStyles, date1904)
      if (ci > maxCol) maxCol = ci
    })

    const out = []
    for (let i = 0; i <= maxCol; i++) out.push(arr[i] == null ? '' : String(arr[i]).trim())
    rows.push(out)
  })

  /* 去掉尾部整行空白 */
  while (rows.length && rows[rows.length - 1].every((x) => !x)) rows.pop()
  /* 去掉尾部整列空白 */
  let width = rows.reduce((m, r) => Math.max(m, r.length), 0)
  while (width > 0 && rows.every((r) => !(r[width - 1] || '').trim())) width--
  return rows.map((r) => {
    const o = r.slice(0, width)
    while (o.length < width) o.push('')
    return o
  })
}

/** 读取整个 .xlsx → [{name, rows}] */
export async function readXlsx(file) {
  const buffer = await file.arrayBuffer()
  const zip = buildZipIndex(buffer)

  const wbXml = await readEntry(zip, 'xl/workbook.xml')
  if (!wbXml) throw new Error('文件里没有 xl/workbook.xml，可能不是 Excel 工作簿')

  const wbDoc = parseXml(wbXml)
  let date1904 = false
  const wbPr = tags(wbDoc, 'workbookPr')[0]
  if (wbPr) {
    const v1904 = wbPr.getAttribute('date1904')
    date1904 = v1904 === '1' || v1904 === 'true'
  }

  /* 关系表：r:id → 实际路径 */
  const relMap = {}
  const relXml = await readEntry(zip, 'xl/_rels/workbook.xml.rels')
  if (relXml) {
    tags(parseXml(relXml), 'Relationship').forEach((r) => {
      relMap[r.getAttribute('Id')] = r.getAttribute('Target')
    })
  }

  const shared = parseSharedStrings(await readEntry(zip, 'xl/sharedStrings.xml'))
  const dateStyles = parseStyles(await readEntry(zip, 'xl/styles.xml'))

  const sheetEls = tags(wbDoc, 'sheet')
  const sheets = []

  for (let i = 0; i < sheetEls.length; i++) {
    const el = sheetEls[i]
    const name = el.getAttribute('name') || `工作表${i + 1}`
    const rid =
      el.getAttribute('r:id') ||
      el.getAttributeNS(
        'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
        'id'
      )
    const target = relMap[rid]

    /* 回退：按顺序猜路径 */
    const path = target
      ? target.charAt(0) === '/'
        ? target.slice(1)
        : `xl/${target.replace(/^\.\//, '')}`
      : `xl/worksheets/sheet${i + 1}.xml`

    let sheetXml = await readEntry(zip, path)
    if (!sheetXml) sheetXml = await readEntry(zip, `xl/worksheets/sheet${i + 1}.xml`)
    if (!sheetXml) {
      sheets.push({ name, rows: [] })
      continue
    }

    sheets.push({ name, rows: parseSheet(sheetXml, shared, dateStyles, date1904) })
  }

  return sheets
}

/* ============================ CSV / TSV 解析 ============================ */

const DELIMS = [',', '\t', ';', '|']

export function detectDelimiter(text) {
  const sample = text.slice(0, 4000)
  const counts = DELIMS.map((d) => {
    let n = 0
    let inQ = false
    for (let i = 0; i < sample.length; i++) {
      const ch = sample[i]
      if (ch === '"') {
        inQ = !inQ
        continue
      }
      if (!inQ && ch === d) n++
    }
    return { d, n }
  })
  counts.sort((a, b) => b.n - a.n)
  return counts[0].n > 0 ? counts[0].d : ','
}

export function parseDelimited(text, delim) {
  if (typeof text !== 'string') text = String(text || '')
  if (text.charCodeAt(0) === 0xfeff) text = text.slice(1)
  if (delim == null) delim = detectDelimiter(text)

  const rows = []
  let row = []
  let field = ''
  let inQ = false

  for (let i = 0; i < text.length; i++) {
    const ch = text.charAt(i)

    if (inQ) {
      if (ch === '"') {
        if (text.charAt(i + 1) === '"') {
          field += '"'
          i++
        } else inQ = false
      } else field += ch
      continue
    }

    if (ch === '"') {
      inQ = true
      continue
    }
    if (ch === delim) {
      row.push(field)
      field = ''
      continue
    }
    if (ch === '\r') continue
    if (ch === '\n') {
      row.push(field)
      rows.push(row)
      row = []
      field = ''
      continue
    }
    field += ch
  }
  row.push(field)
  rows.push(row)

  /* 去掉尾部空行 */
  while (rows.length && rows[rows.length - 1].every((x) => !String(x).trim())) rows.pop()

  const width = rows.reduce((m, r) => Math.max(m, r.length), 0)
  return rows.map((r) => {
    const o = r.map((x) => String(x).trim())
    while (o.length < width) o.push('')
    return o
  })
}

/* ============================ 对外读取接口 ============================ */

function readText(file) {
  return new Promise((resolve, reject) => {
    const fr = new FileReader()
    fr.onload = () => resolve(String(fr.result))
    fr.onerror = () => reject(new Error('读取文件失败'))
    fr.readAsText(file, 'utf-8')
  })
}

/** 读一个 .xlsx / .csv 文件 → { kind: 'table', sheets: [{name, rows}] } */
export async function readFile(file) {
  const name = (file.name || '').toLowerCase()
  const type = (file.type || '').toLowerCase()

  if (/\.xlsx$/.test(name) || type.includes('spreadsheetml')) {
    return { kind: 'table', sheets: await readXlsx(file) }
  }

  if (/\.xls$/.test(name) && !/\.xlsx$/.test(name)) {
    throw new Error('不支持旧版 .xls 格式。请在 Excel 中「另存为」为 .xlsx，或另存为「CSV UTF-8（逗号分隔）」后重试。')
  }

  const text = await readText(file)
  /* 兜底：有些 .xlsx 被改了扩展名，实际是 ZIP */
  if (text.charCodeAt(0) === 0x50 && text.charCodeAt(1) === 0x4b) {
    return { kind: 'table', sheets: await readXlsx(file) }
  }
  return { kind: 'table', sheets: [{ name: file.name || 'CSV', rows: parseDelimited(text) }] }
}

/** 解析从 Excel 直接粘贴的文本（优先制表符） */
export function parseClipboard(text) {
  let d = detectDelimiter(text)
  if (text.indexOf('\t') >= 0) d = '\t'
  return parseDelimited(text, d)
}

/* ============================ 写出 .xlsx ============================
   单元格取值支持三种：
     number            → 数值单元格
     { date: 'Y-M-D' } → 真正的日期单元格（带日期格式，Excel 可排序 / 筛选）
     其它              → 文本（共享字符串）
   ==================================================================== */

const CRC_TABLE = (() => {
  const t = new Int32Array(256)
  for (let n = 0; n < 256; n++) {
    let c = n
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    t[n] = c
  }
  return t
})()

function crc32(bytes) {
  let c = -1
  for (let i = 0; i < bytes.length; i++) c = CRC_TABLE[(c ^ bytes[i]) & 0xff] ^ (c >>> 8)
  return (c ^ -1) >>> 0
}

/** 用 CompressionStream 做 raw deflate；不可用时返回 null（退回不压缩存储） */
async function deflateRaw(bytes) {
  if (typeof CompressionStream !== 'function') return null
  try {
    const stream = new Blob([bytes]).stream().pipeThrough(new CompressionStream('deflate-raw'))
    return new Uint8Array(await new Response(stream).arrayBuffer())
  } catch {
    return null
  }
}

function concatBytes(list) {
  let len = 0
  for (let i = 0; i < list.length; i++) len += list[i].length
  const out = new Uint8Array(len)
  let p = 0
  for (let i = 0; i < list.length; i++) {
    out.set(list[i], p)
    p += list[i].length
  }
  return out
}

async function buildZip(entries) {
  const enc = new TextEncoder()
  const chunks = []
  const central = []
  let offset = 0

  for (let i = 0; i < entries.length; i++) {
    const e = entries[i]
    const nameBytes = enc.encode(e.name)
    const crc = crc32(e.bytes)
    const comp = await deflateRaw(e.bytes)
    const method = comp ? 8 : 0
    const data = comp || e.bytes

    const lfh = new Uint8Array(30 + nameBytes.length)
    const dv = new DataView(lfh.buffer)
    dv.setUint32(0, 0x04034b50, true)
    dv.setUint16(4, 20, true)
    dv.setUint16(6, 0x0800, true) /* UTF-8 文件名标志 */
    dv.setUint16(8, method, true)
    dv.setUint16(10, 0, true)
    dv.setUint16(12, 0x5a21, true)
    dv.setUint32(14, crc, true)
    dv.setUint32(18, data.length, true)
    dv.setUint32(22, e.bytes.length, true)
    dv.setUint16(26, nameBytes.length, true)
    dv.setUint16(28, 0, true)
    lfh.set(nameBytes, 30)
    chunks.push(lfh, data)

    const cd = new Uint8Array(46 + nameBytes.length)
    const cv = new DataView(cd.buffer)
    cv.setUint32(0, 0x02014b50, true)
    cv.setUint16(4, 20, true)
    cv.setUint16(6, 20, true)
    cv.setUint16(8, 0x0800, true)
    cv.setUint16(10, method, true)
    cv.setUint16(12, 0, true)
    cv.setUint16(14, 0x5a21, true)
    cv.setUint32(16, crc, true)
    cv.setUint32(20, data.length, true)
    cv.setUint32(24, e.bytes.length, true)
    cv.setUint16(28, nameBytes.length, true)
    cv.setUint16(30, 0, true)
    cv.setUint16(32, 0, true)
    cv.setUint16(34, 0, true)
    cv.setUint16(36, 0, true)
    cv.setUint32(38, 0, true)
    cv.setUint32(42, offset, true)
    cd.set(nameBytes, 46)
    central.push(cd)

    offset += lfh.length + data.length
  }

  const cdBytes = concatBytes(central)
  const eocd = new Uint8Array(22)
  const ev = new DataView(eocd.buffer)
  ev.setUint32(0, 0x06054b50, true)
  ev.setUint16(4, 0, true)
  ev.setUint16(6, 0, true)
  ev.setUint16(8, entries.length, true)
  ev.setUint16(10, entries.length, true)
  ev.setUint32(12, cdBytes.length, true)
  ev.setUint32(16, offset, true)
  ev.setUint16(20, 0, true)

  return new Blob(chunks.concat([cdBytes, eocd]), {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  })
}

function xmlEsc(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&apos;')
    /* XML 1.0 不允许的控制字符，直接剔除 */
    .replace(/[\x00-\x08\x0b\x0c\x0e-\x1f]/g, '')
}

function colName(n) {
  let s = ''
  n++
  while (n > 0) {
    const r = (n - 1) % 26
    s = String.fromCharCode(65 + r) + s
    n = (n - r - 1) / 26
  }
  return s
}

function isoToSerial(iso) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(iso || ''))
  if (!m) return null
  return Math.round(Date.UTC(+m[1], +m[2] - 1, +m[3]) / 86400000) + 25569
}

function safeSheetName(name, idx) {
  /* Excel 工作表名不允许这 7 个字符，且上限 31 个字符 */
  const s = String(name == null ? '' : name)
    .replace(/[\[\]\*\?\/\\:]/g, ' ')
    .trim()
    .slice(0, 31)
  return s || `Sheet${idx + 1}`
}
/**
 * 生成 .xlsx（多工作表）
 * @param {Array<{name: string, rows: Array<Array>}>} sheets
 * @param {{colWidths?: number[], autoFilter?: boolean, freezeHeader?: boolean}} opts
 * @returns {Promise<Blob>}
 */
export async function writeXlsx(sheets, opts) {
  opts = opts || {}
  sheets = sheets && sheets.length ? sheets : [{ name: 'Sheet1', rows: [] }]

  const X = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
  const NS_MAIN = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
  const NS_REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'
  const NS_PKG = 'http://schemas.openxmlformats.org/package/2006/relationships'
  const NS_CT = 'http://schemas.openxmlformats.org/package/2006/content-types'

  /* 共享字符串表 */
  const shared = []
  const sstMap = new Map()
  function sid(s) {
    if (!sstMap.has(s)) {
      sstMap.set(s, shared.length)
      shared.push(s)
    }
    return sstMap.get(s)
  }

  const sheetXmls = []

  sheets.forEach((sheet) => {
    const rows = sheet.rows || []
    const width = rows.reduce((m, r) => Math.max(m, r.length), 0)

    /* 先把本表的字符串登记进共享表，再生成 XML */
    const body = rows
      .map((row, ri) => {
        const cells = []
        for (let ci = 0; ci < width; ci++) {
          const v = row[ci]
          if (v == null || v === '') continue
          const ref = colName(ci) + (ri + 1)

          if (typeof v === 'number' && Number.isFinite(v)) {
            cells.push(`<c r="${ref}"><v>${v}</v></c>`)
          } else if (v && typeof v === 'object' && v.date) {
            const ser = isoToSerial(v.date)
            if (ser != null) cells.push(`<c r="${ref}" s="1"><v>${ser}</v></c>`)
          } else {
            /* 首行用加粗样式 */
            cells.push(`<c r="${ref}"${ri === 0 ? ' s="2"' : ''} t="s"><v>${sid(String(v))}</v></c>`)
          }
        }
        return `<row r="${ri + 1}">${cells.join('')}</row>`
      })
      .join('')

    let colsXml = ''
    if (opts.colWidths && opts.colWidths.length) {
      const parts = []
      for (let i = 0; i < Math.min(width, opts.colWidths.length); i++) {
        const w = opts.colWidths[i]
        if (w) parts.push(`<col min="${i + 1}" max="${i + 1}" width="${w}" customWidth="1"/>`)
      }
      if (parts.length) colsXml = `<cols>${parts.join('')}</cols>`
    }

    const lastRef = width ? colName(width - 1) + Math.max(rows.length, 1) : 'A1'
    const view =
      opts.freezeHeader === false
        ? ''
        : '<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'

    sheetXmls.push(
      X +
        `<worksheet xmlns="${NS_MAIN}">` +
        `<dimension ref="A1:${lastRef}"/>` +
        `<sheetViews><sheetView workbookViewId="0">${view}</sheetView></sheetViews>` +
        '<sheetFormatPr defaultRowHeight="15"/>' +
        colsXml +
        `<sheetData>${body}</sheetData>` +
        (opts.autoFilter !== false && width && rows.length ? `<autoFilter ref="A1:${lastRef}"/>` : '') +
        '</worksheet>'
    )
  })

  const sheetMetas = sheets.map((s, i) => ({
    name: safeSheetName(s.name, i),
    id: i + 1,
    rid: `rId${i + 1}`,
  }))

  const contentTypes =
    X +
    `<Types xmlns="${NS_CT}">` +
    '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>' +
    '<Default Extension="xml" ContentType="application/xml"/>' +
    '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>' +
    sheetMetas
      .map(
        (m) =>
          `<Override PartName="/xl/worksheets/sheet${m.id}.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>`
      )
      .join('') +
    '<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>' +
    '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>' +
    '</Types>'

  const rootRels =
    X +
    `<Relationships xmlns="${NS_PKG}">` +
    `<Relationship Id="rId1" Type="${NS_REL}/officeDocument" Target="xl/workbook.xml"/>` +
    '</Relationships>'

  const workbook =
    X +
    `<workbook xmlns="${NS_MAIN}" xmlns:r="${NS_REL}">` +
    '<sheets>' +
    sheetMetas
      .map((m) => `<sheet name="${xmlEsc(m.name)}" sheetId="${m.id}" r:id="${m.rid}"/>`)
      .join('') +
    '</sheets></workbook>'

  const wbRels =
    X +
    `<Relationships xmlns="${NS_PKG}">` +
    sheetMetas
      .map(
        (m) =>
          `<Relationship Id="${m.rid}" Type="${NS_REL}/worksheet" Target="worksheets/sheet${m.id}.xml"/>`
      )
      .join('') +
    `<Relationship Id="rIdS" Type="${NS_REL}/sharedStrings" Target="sharedStrings.xml"/>` +
    `<Relationship Id="rIdT" Type="${NS_REL}/styles" Target="styles.xml"/>` +
    '</Relationships>'

  const sharedXml =
    X +
    `<sst xmlns="${NS_MAIN}" count="${shared.length}" uniqueCount="${shared.length}">` +
    shared.map((s) => `<si><t xml:space="preserve">${xmlEsc(s)}</t></si>`).join('') +
    '</sst>'

  /* 样式：0=常规 1=日期(yyyy-mm-dd) 2=表头加粗 */
  const styles =
    X +
    `<styleSheet xmlns="${NS_MAIN}">` +
    '<numFmts count="1"><numFmt numFmtId="164" formatCode="yyyy\\-mm\\-dd"/></numFmts>' +
    '<fonts count="2">' +
    '<font><sz val="11"/><color theme="1"/><name val="Calibri"/></font>' +
    '<font><b/><sz val="11"/><color theme="1"/><name val="Calibri"/></font>' +
    '</fonts>' +
    '<fills count="2">' +
    '<fill><patternFill patternType="none"/></fill>' +
    '<fill><patternFill patternType="gray125"/></fill>' +
    '</fills>' +
    '<borders count="1"><border/></borders>' +
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>' +
    '<cellXfs count="3">' +
    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>' +
    '<xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>' +
    '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0" applyFont="1"/>' +
    '</cellXfs>' +
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles>' +
    '</styleSheet>'

  const enc = new TextEncoder()
  const entries = [
    { name: '[Content_Types].xml', bytes: enc.encode(contentTypes) },
    { name: '_rels/.rels', bytes: enc.encode(rootRels) },
    { name: 'xl/workbook.xml', bytes: enc.encode(workbook) },
    { name: 'xl/_rels/workbook.xml.rels', bytes: enc.encode(wbRels) },
    { name: 'xl/sharedStrings.xml', bytes: enc.encode(sharedXml) },
    { name: 'xl/styles.xml', bytes: enc.encode(styles) },
  ]
  sheetXmls.forEach((xml, i) => {
    entries.push({ name: `xl/worksheets/sheet${i + 1}.xml`, bytes: enc.encode(xml) })
  })

  return buildZip(entries)
}

/** 触发浏览器下载一个 Blob / 字符串（导出的最后一步，与页面解耦） */
export function downloadBlob(filename, content, mime) {
  const blob =
    content instanceof Blob ? content : new Blob([content], { type: mime || 'text/plain;charset=utf-8' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  setTimeout(() => URL.revokeObjectURL(url), 1500)
}
