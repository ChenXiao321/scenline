// 步骤关联解析：把工具原型规格里的 flowSteps（如 "步骤 1-2" / "步骤 3"）解析成步骤号数组
// 支持中文/英文逗号、连字符范围、纯数字、多个组合

/**
 * 解析 flowSteps 文本为步骤号数组
 * @param {string} flowSteps 如 "步骤 1-2"、"步骤 3"、"1,2,4"、"步骤 5"
 * @returns {number[]} 步骤号数组（去重、升序）
 */
export function parseFlowSteps(flowSteps) {
  if (!flowSteps) return []
  const s = String(flowSteps)
  const out = new Set()
  // 匹配所有数字及 "a-b" 范围
  const rangeRe = /(\d+)\s*[-–—~至到]\s*(\d+)/g
  let m
  const consumed = []
  while ((m = rangeRe.exec(s)) !== null) {
    const a = parseInt(m[1], 10), b = parseInt(m[2], 10)
    for (let i = Math.min(a, b); i <= Math.max(a, b); i++) out.add(i)
    consumed.push(m[0])
  }
  // 去掉已匹配的范围后，再匹配剩余独立数字
  const rest = consumed.reduce((acc, c) => acc.replace(c, ' '), s)
  const numRe = /\d+/g
  while ((m = numRe.exec(rest)) !== null) out.add(parseInt(m[0], 10))
  return [...out].sort((x, y) => x - y)
}

/**
 * 从 ui-spec 的 screens 建立 步骤号 -> [屏索引] 的反查表
 * @param {object} uiSpec 工具原型规格
 * @returns {Map<number, number[]>} step -> [screenIdx, ...]
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
