/**
 * A deliberately SMALL JSON Schema (draft 2020-12) evaluator -- exactly the
 * keywords the vendored `axr-assertions/1` schema uses
 * (`axrange/api/schemas/axr-assertions-1.schema.json`), so a Console test can
 * validate a document against THAT FILE rather than against a restatement of
 * its rules. The package carries no schema-validator dependency, and adding one
 * is not this slice's to decide.
 *
 * **It fails loudly on a keyword it does not implement** -- an evaluator that
 * skipped an unknown keyword would call a document valid against a rule it never
 * read, which is the silent pass this exists to rule out (the positive-gate rule
 * of LES-087). Its own refusals are pinned by `batch.roundtrip.test.ts` against
 * known-bad documents, so a vacuous evaluator cannot hide behind a green run.
 */

export type SchemaNode = Record<string, unknown> | boolean;

/** Annotation keywords: they constrain nothing (`format` is annotation-only in
 *  2020-12 unless a vocabulary opts in, and the schema's own `$comment` says so). */
const ANNOTATIONS = new Set(["$comment", "description", "title", "format", "$id", "$schema", "$defs"]);

const ASSERTIONS = new Set([
  "$ref",
  "type",
  "properties",
  "required",
  "additionalProperties",
  "items",
  "minItems",
  "minProperties",
  "propertyNames",
  "pattern",
  "enum",
  "const",
  "oneOf",
  "allOf",
  "if",
  "then",
  "minimum",
  "maximum",
]);

/** Every error `instance` has against `root`, as `path: why`; `[]` when valid. */
export function validateAgainst(root: Record<string, unknown>, instance: unknown): string[] {
  return check(root, root, instance, "$");
}

function check(root: Record<string, unknown>, node: SchemaNode, value: unknown, path: string): string[] {
  if (node === true) return [];
  if (node === false) return [`${path}: the schema allows nothing here`];
  for (const key of Object.keys(node)) {
    if (!ANNOTATIONS.has(key) && !ASSERTIONS.has(key)) {
      throw new Error(`unsupported JSON Schema keyword "${key}" at ${path} -- implement it before trusting a pass`);
    }
  }
  const errors: string[] = [];

  if (typeof node.$ref === "string") errors.push(...check(root, resolve(root, node.$ref), value, path));

  if (node.type !== undefined) {
    const types = Array.isArray(node.type) ? (node.type as string[]) : [node.type as string];
    if (!types.some((t) => hasType(value, t))) errors.push(`${path}: expected ${types.join(" | ")}, got ${typeName(value)}`);
  }
  if (Array.isArray(node.enum) && !node.enum.some((v) => deepEqual(v, value))) {
    errors.push(`${path}: ${JSON.stringify(value)} is not one of ${JSON.stringify(node.enum)}`);
  }
  if ("const" in node && !deepEqual(node.const, value)) {
    errors.push(`${path}: expected ${JSON.stringify(node.const)}, got ${JSON.stringify(value)}`);
  }
  if (typeof node.pattern === "string" && typeof value === "string" && !regexOf(node.pattern).test(value)) {
    errors.push(`${path}: ${JSON.stringify(value)} does not match ${node.pattern}`);
  }
  if (typeof value === "number") {
    if (typeof node.minimum === "number" && value < node.minimum) errors.push(`${path}: ${value} < ${node.minimum}`);
    if (typeof node.maximum === "number" && value > node.maximum) errors.push(`${path}: ${value} > ${node.maximum}`);
  }

  if (isObject(value)) {
    const props = (node.properties ?? {}) as Record<string, SchemaNode>;
    for (const [name, sub] of Object.entries(props)) {
      if (name in value) errors.push(...check(root, sub, value[name], `${path}.${name}`));
    }
    for (const name of (node.required ?? []) as string[]) {
      if (!(name in value)) errors.push(`${path}: missing required "${name}"`);
    }
    if (node.additionalProperties !== undefined) {
      for (const name of Object.keys(value)) {
        if (name in props) continue;
        errors.push(...check(root, node.additionalProperties as SchemaNode, value[name], `${path}.${name}`));
      }
    }
    if (node.propertyNames !== undefined) {
      for (const name of Object.keys(value)) {
        errors.push(...check(root, node.propertyNames as SchemaNode, name, `${path}{${name}}`));
      }
    }
    if (typeof node.minProperties === "number" && Object.keys(value).length < node.minProperties) {
      errors.push(`${path}: fewer than ${node.minProperties} properties`);
    }
  }

  if (Array.isArray(value)) {
    if (node.items !== undefined) {
      value.forEach((item, i) => errors.push(...check(root, node.items as SchemaNode, item, `${path}[${i}]`)));
    }
    if (typeof node.minItems === "number" && value.length < node.minItems) {
      errors.push(`${path}: fewer than ${node.minItems} items`);
    }
  }

  if (Array.isArray(node.allOf)) {
    for (const sub of node.allOf as SchemaNode[]) errors.push(...check(root, sub, value, path));
  }
  if (Array.isArray(node.oneOf)) {
    const passing = (node.oneOf as SchemaNode[]).filter((sub) => check(root, sub, value, path).length === 0).length;
    if (passing !== 1) errors.push(`${path}: matches ${passing} of the oneOf branches, not exactly one`);
  }
  if (node.if !== undefined && check(root, node.if as SchemaNode, value, path).length === 0 && node.then !== undefined) {
    errors.push(...check(root, node.then as SchemaNode, value, path));
  }
  return errors;
}

function resolve(root: Record<string, unknown>, ref: string): SchemaNode {
  if (!ref.startsWith("#/")) throw new Error(`only local $refs are supported, got ${ref}`);
  let at: unknown = root;
  for (const part of ref.slice(2).split("/")) {
    if (!isObject(at) || !(part in at)) throw new Error(`unresolvable $ref ${ref}`);
    at = at[part];
  }
  return at as SchemaNode;
}

function hasType(value: unknown, type: string): boolean {
  switch (type) {
    case "object":
      return isObject(value);
    case "array":
      return Array.isArray(value);
    case "string":
      return typeof value === "string";
    case "integer":
      return typeof value === "number" && Number.isInteger(value);
    case "number":
      return typeof value === "number" && Number.isFinite(value);
    case "boolean":
      return typeof value === "boolean";
    case "null":
      return value === null;
    default:
      throw new Error(`unsupported JSON Schema type "${type}"`);
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function typeName(value: unknown): string {
  return value === null ? "null" : Array.isArray(value) ? "array" : typeof value;
}

function deepEqual(a: unknown, b: unknown): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

const regexCache = new Map<string, RegExp>();

/** ECMA-262 regular expressions, unanchored (JSON Schema's `pattern` rule). */
function regexOf(pattern: string): RegExp {
  let re = regexCache.get(pattern);
  if (!re) {
    re = new RegExp(pattern, "u");
    regexCache.set(pattern, re);
  }
  return re;
}
