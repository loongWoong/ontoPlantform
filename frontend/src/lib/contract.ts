/** 能力执行契约的编辑与校验（与后端 Capability contract 同名）。
 *  契约描述的是执行前后的世界状态，不是输入参数 JSON Schema。 */

export const CONTRACT_SECTIONS = [
  "preconditions", "postconditions", "effects", "idempotent", "compensate",
] as const;

export type ContractSection = typeof CONTRACT_SECTIONS[number];

/** 计划要求的示例形状：后置条件引用输入参数，compensate 为空表示不声明补偿。 */
export const CONTRACT_EXAMPLE: Record<string, unknown> = {
  preconditions: [{ property: "status", op: "eq", value: "active" }],
  postconditions: [{
    property: "balance",
    op: "eq_prev_plus",
    value: { args: "amount" },
  }],
  effects: ["balance"],
  idempotent: true,
  compensate: "",
};

export function contractExampleText(): string {
  return JSON.stringify(CONTRACT_EXAMPLE, null, 2);
}

export interface ParsedContract {
  value?: Record<string, unknown>;
  error?: string;
}

/** 客户端只做“形状与顶层键”检查；语义校验交给后端，错误原样呈现。 */
export function parseContract(text: string): ParsedContract {
  const raw = (text || "").trim();
  if (!raw) return {};
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return { error: "契约不是合法 JSON" };
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    return { error: "契约要是一个 JSON 对象" };
  }
  const obj = parsed as Record<string, unknown>;
  const unknownKeys = Object.keys(obj).filter(k => !CONTRACT_SECTIONS.includes(k as ContractSection));
  if (unknownKeys.length) {
    return {
      error: `不认识的契约顶层键：${unknownKeys.join("、")}；允许 ${CONTRACT_SECTIONS.join("、")}`,
    };
  }
  if (obj.preconditions !== undefined && !Array.isArray(obj.preconditions)) {
    return { error: "preconditions 要是数组" };
  }
  if (obj.postconditions !== undefined && !Array.isArray(obj.postconditions)) {
    return { error: "postconditions 要是数组" };
  }
  if (obj.effects !== undefined && !Array.isArray(obj.effects)) {
    return { error: "effects 要是数组" };
  }
  if (obj.idempotent !== undefined && typeof obj.idempotent !== "boolean") {
    return { error: "idempotent 要是布尔值" };
  }
  return { value: obj };
}
