/**
 * A filter's options, from the document's metadata and from a code block's
 * YAML header, read by pandoc (panflute's `yaml_filter`).
 *
 * `yamlBlock` splits a block's text into its header and its data, by
 * pandoc's rule for a YAML metadata block; pandoc reads the header (panir
 * reads no YAML); `Options.of` puts it over the document's metadata under
 * the filter's name; `yamlFilter` does it all for a document, with one call
 * of the reader for all of its blocks.
 */

import { deep, ownerOf, setKey } from "./core.ts";
import type { Blocks, CodeBlock, Meta, MetaValue, Pandoc } from "./generated.ts";
import { luaNumber, stringify } from "./util.ts";
import { applyFilter } from "./walk.ts";

const OPENING = /^---[ \t]*$/;
const CLOSING = /^(?:---|\.\.\.)[ \t]*$/;
const BLANK = /^[ \t]*$/;

/**
 * A text's YAML header and its data, by pandoc's rule for a YAML metadata
 * block, where one starts the text: the first line is `---`, the next is
 * not blank, and a later line is `---` or `...` (each perhaps followed by
 * spaces or tabs; not indented, not four of them). The header is the lines
 * between, each with its newline (`""` for `---` then `---`); the data is
 * everything after the closing line. No header: `[undefined, text]`. A
 * `---` line later in the text opens nothing: text appended to the block
 * (an include) is never read as options.
 *
 * ```ts
 * yamlBlock("---\ncaption: Sales\n---\na,b\n"); // ["caption: Sales\n", "a,b\n"]
 * ```
 */
export function yamlBlock(text: string): [header: string | undefined, data: string] {
  const newline = text.indexOf("\n");
  if (newline < 0 || !OPENING.test(text.slice(0, newline))) return [undefined, text];
  const start = newline + 1;
  for (let at = start; ;) {
    const end = text.indexOf("\n", at);
    const line = end < 0 ? text.slice(at) : text.slice(at, end);
    if (at === start && BLANK.test(line)) return [undefined, text];
    if (CLOSING.test(line)) return [text.slice(start, at), end < 0 ? "" : text.slice(end + 1)];
    if (end < 0) return [undefined, text];
    at = end + 1;
  }
}

/** Metadata as plain data (`toData`'s). */
export type DataValue = string | boolean | DataValue[] | { [key: string]: DataValue };

/**
 * Metadata as plain data, as a filter reads its options: a `MetaBool` a
 * boolean, a `MetaString` a string, `MetaInlines` and `MetaBlocks` their
 * text (`stringify`), a `MetaList` an array, a `MetaMap` (or the metadata,
 * or `Options`) an object (panflute's `get_metadata(builtin=True)`).
 * Nothing is guessed: text stays text, `"10"` too (`luaNumber` reads a
 * number). `toPlain` keeps inlines and blocks as nodes instead.
 */
export function toData(value: MetaValue): DataValue;
export function toData(value: Meta | Options): { [key: string]: DataValue };
export function toData(value: MetaValue | Meta | Options): DataValue {
  return deep("toData", () => data(value instanceof Options ? value.values : value));
}

function data(value: MetaValue | Meta): DataValue {
  if (ownerOf(value) !== "MetaValue") return mapData(value as Meta);
  const v = value as MetaValue;
  switch (v.t) {
    case "MetaBool":
      return v.value;
    case "MetaString":
      return v.text;
    case "MetaInlines":
    case "MetaBlocks":
      return stringify(v);
    case "MetaList":
      return v.content.map(data);
    case "MetaMap":
      return mapData(v.content);
  }
}

function mapData(m: Meta): { [key: string]: DataValue } {
  const out: { [key: string]: DataValue } = {};
  for (const [k, v] of Object.entries(m)) if (v !== undefined) setKey(out, k, data(v));
  return out;
}

/**
 * A filter's options: metadata values by key, read raw (`get`: a caption
 * wants its inlines) or converted on request: `text`, `boolean`, `number`,
 * `list`, `map`, each `undefined` where the key is missing or its value is
 * of another shape.
 *
 * What pandoc makes of a header (read as a metadata block, `-f markdown`;
 * checked with pandoc 3.14): `true` and `True` are booleans (`yes` is
 * text); numbers are text, as pandoc writes them (`010` is `"10"`, `1e3`
 * `"1000"`); `null` and `~` are empty text; text is read as markdown, so a
 * caption arrives as inlines, but a text of only a tab is lost, `'` is a
 * curly quote and `...` an ellipsis (`-f markdown-smart` keeps them),
 * escapes, entities and raw HTML are read; a `|` scalar is blocks.
 */
export class Options {
  /** The values by key (a map without a prototype): the documents' own, not copies. */
  readonly values: Meta;

  constructor(values: Meta = {}) {
    this.values = Object.create(null) as Meta;
    for (const [k, v] of Object.entries(values)) setKey(this.values, k, v);
  }

  /**
   * The options under `name` in the document's metadata (`meta`; ignored
   * unless a map), each key's value replaced whole by the header's
   * (`header`: a block's header, read as a document's metadata), as pandoc
   * merges metadata blocks: no deeper merge.
   *
   * ```ts
   * const options = Options.of(doc.meta, "pantable", headerDoc.meta);
   * ```
   */
  static of(meta: Meta, name: string, header?: Meta): Options {
    const defaults = Object.hasOwn(meta, name) ? meta[name] : undefined;
    const options = new Options(defaults?.t === "MetaMap" ? defaults.content : {});
    for (const [k, v] of Object.entries(header ?? {})) setKey(options.values, k, v);
    return options;
  }

  /** The value of `key`, as it is. */
  get(key: string): MetaValue | undefined {
    return Object.hasOwn(this.values, key) ? this.values[key] : undefined;
  }

  /** Text (a `MetaString`), or the text of inlines or blocks. */
  text(key: string): string | undefined {
    const v = this.get(key);
    if (v?.t === "MetaString") return v.text;
    return v?.t === "MetaInlines" || v?.t === "MetaBlocks" ? stringify(v) : undefined;
  }

  /** A `MetaBool`'s value. */
  boolean(key: string): boolean | undefined {
    const v = this.get(key);
    return v?.t === "MetaBool" ? v.value : undefined;
  }

  /** Text read as a number, as `luaNumber` reads it. */
  number(key: string): number | undefined {
    const text = this.text(key);
    return text === undefined ? undefined : luaNumber(text);
  }

  /** A `MetaList`'s items, as plain data (`toData`). */
  list(key: string): DataValue[] | undefined {
    const v = this.get(key);
    return v?.t === "MetaList" ? (toData(v) as DataValue[]) : undefined;
  }

  /** A `MetaMap`, as plain data (`toData`). */
  map(key: string): { [key: string]: DataValue } | undefined {
    const v = this.get(key);
    return v?.t === "MetaMap" ? toData(v.content) : undefined;
  }
}

/**
 * Code blocks with a YAML header, panflute's `yaml_filter`: each code block
 * with one of `classes` is replaced by what `fn(options, data, block)`
 * returns, as a filter function's (nothing keeps it, a block replaces it,
 * a list is spliced, `[]` deletes it). `options` is `Options.of(doc.meta,
 * name, header)`, `data` the text after the header (`yamlBlock`), or all
 * of it.
 *
 * `read` reads texts of markdown into documents, one each: the host's
 * (`readMany`, its results through `fromJSON`), or the `pandoc` command.
 * It is called once, with every header as a document (`---`, the header,
 * `---`); not at all if no block has one. A header pandoc reads no
 * metadata from (a YAML scalar or list: blocks come back) is no header,
 * and the whole text is data, as pandoc would read it. The document is
 * changed in place, and returned.
 *
 * ```ts
 * const tables = libpandocFilter((pandoc: Host) => [{
 *   Pandoc: (doc, ctx) => yamlFilter(doc, "table", "pantable", (texts) => read(pandoc, texts, ctx.conversion), table),
 * }]);
 * ```
 */
export function yamlFilter(
  doc: Pandoc,
  classes: string | readonly string[],
  name: string,
  read: (texts: string[]) => readonly Pandoc[],
  fn: (options: Options, data: string, block: CodeBlock) => Blocks | void | undefined | null,
): Pandoc {
  const wanted = typeof classes === "string" ? [classes] : classes;
  const found: CodeBlock[] = [];
  applyFilter(doc, {
    CodeBlock: (block) => {
      if (block.attr.classes.some((c) => wanted.includes(c))) found.push(block);
    },
  });
  const splits = found.map((block) => yamlBlock(block.text));
  const headers = splits.flatMap(([header]) => (header === undefined ? [] : [`---\n${header}---\n`]));
  const docs = headers.length > 0 ? read(headers) : [];
  const todo = new Map<CodeBlock, [Options, string]>();
  let k = 0;
  found.forEach((block, i) => {
    const header = splits[i]![0];
    let data = splits[i]![1];
    let meta: Meta | undefined;
    if (header !== undefined) {
      const got = docs[k++]!;
      if (got.blocks.length > 0) data = block.text;
      else meta = got.meta;
    }
    todo.set(block, [Options.of(doc.meta, name, meta), data]);
  });
  return applyFilter(doc, {
    CodeBlock: (block) => {
      const args = todo.get(block);
      return args ? fn(args[0], args[1], block) : undefined;
    },
  });
}
