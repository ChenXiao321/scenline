// 步骤关联解析：把工具原型规格里的 flowSteps（如 "步骤 1-2" / "步骤 3" / "3a"）解析成步骤号数组
// 支持中文/英文逗号、连字符范围、纯数字、变体号（数字+字母后缀）、多个组合
// 步骤号统一为字符串："3a" 这类变体号不能用数字表示（Number("3a")=NaN）

/**
 * 解析 flowSteps 文本为步骤号数组
 * @param {string} flowSteps 如 "步骤 1-2"、"步骤 3"、"1,2,4"、"3a"、"步骤 3a,3b"
 * @returns {string[]} 步骤号数组（去重，数字部分升序）
 */
export function parseFlowSteps(flowSteps) {
  if (!flowSteps) return []
  const s = String(flowSteps)
  const out = new Set()
  // 先匹配 "a-b" 数字范围（如 1-3 / 1–3 / 1至3）
  const rangeRe = /(\d+)\s*[-–—~至到]\s*(\d+)/g
  let m
  const consumed = []
  while ((m = rangeRe.exec(s)) !== null) {
    const a = parseInt(m[1], 10), b = parseInt(m[2], 10)
    for (let i = Math.min(a, b); i <= Math.max(a, b); i++) out.add(String(i))
    consumed.push(m[0])
  }
  // 去掉已匹配范围后，再匹配剩余步骤号：纯数字或数字+字母后缀（变体号）
  const rest = consumed.reduce((acc, c) => acc.replace(c, ' '), s)
  const noRe = /\d+[a-zA-Z]?/g
  while ((m = noRe.exec(rest)) !== null) out.add(m[0])
  return [...out].sort((x, y) => parseInt(x, 10) - parseInt(y, 10) || x.localeCompare(y))
}

/**
 * 从 ui-spec 的 screens 建立 步骤号 -> [屏索引] 的反查表
 * @param {object} uiSpec 工具原型规格
 * @returns {Map<string, number[]>} step(字符串) -> [screenIdx, ...]
 */
export function stepToScreens(uiSpec) {
  const map = new Map()
  const screens = uiSpec?.screens || []
  screens.forEach((sc, i) => {
    for (const n of parseFlowSteps(sc.flowSteps)) {
      if (!map.has(n)) map.set(n, [])
      map.get(n).push(i)
    }
  })
  return map
}
