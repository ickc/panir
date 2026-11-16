/**
 * The pandoc conversion a filter runs in, as panir's Python `Conversion`.
 *
 * A filter function finds it in `ctx.conversion`: the output format's name
 * (`format`, what pandoc passes a JSON filter), and, when known, the input
 * and output formats with extensions, the reader's and the writer's options,
 * whether pandoc's sandbox is on, and the conversion's options
 * (defaults-file keys). Who runs the filter decides how much is known:
 *
 * - as a JSON filter under pandoc (`runFilter`): the output format's name
 *   and the reader's options (`$PANDOC_READER_OPTIONS`); and what libpandoc
 *   sets for the JSON filters it runs: the formats with extensions
 *   (`$PANDOC_INPUT_FORMAT`, `$PANDOC_OUTPUT_FORMAT`; proposed to pandoc in
 *   jgm/pandoc#11016), the writer's options (`$PANDOC_WRITER_OPTIONS`) and
 *   whether pandoc's sandbox is on (`$PANDOC_SANDBOX`);
 * - in process, with libpandoc: what its filter context says
 *   (`fromContext`, `libpandocFilter`).
 */
export interface ConversionInit {
  format?: string;
  inputFormat?: string;
  outputFormat?: string;
  readerOptions?: Record<string, unknown>;
  writerOptions?: Record<string, unknown>;
  options?: Record<string, unknown>;
  sandbox?: boolean;
}

export class Conversion {
  /** The output format's name, e.g. `"html5"`. */
  readonly format: string | undefined;
  /** The formats with extensions, e.g. `"commonmark_x-smart"`, when known. */
  readonly inputFormat: string | undefined;
  readonly outputFormat: string | undefined;
  /** The reader's options (pandoc's `PANDOC_READER_OPTIONS`), when known. */
  readonly readerOptions: Record<string, unknown> | undefined;
  /**
   * The writer's options (Lua's `PANDOC_WRITER_OPTIONS`), when known: keyed
   * as pandoc's JSON keys the reader's (Lua's `number_sections` is
   * `"number-sections"`), with Lua's values (`"top-level-chapter"`).
   */
  readonly writerOptions: Record<string, unknown> | undefined;
  /** The conversion's options, in defaults-file keys, when known. */
  readonly options: Record<string, unknown> | undefined;
  /**
   * Whether pandoc's sandbox is on for the conversion (however it was set:
   * `--sandbox`, the options, a defaults file), as libpandoc (1.13 and
   * later) tells the filters it runs; `undefined` where nothing said (plain
   * pandoc tells its filters nothing of it). A host reading fragments
   * through the conversion (libpandoc's `pandoc.read(text, ctx.conversion)`)
   * reads them in pandoc's sandbox when it is on, or when `undefined`: what
   * can't be told counts as sandboxed.
   */
  readonly sandbox: boolean | undefined;

  constructor(init: ConversionInit = {}) {
    this.format = init.format;
    this.inputFormat = init.inputFormat;
    this.outputFormat = init.outputFormat;
    this.readerOptions = init.readerOptions;
    this.writerOptions = init.writerOptions;
    this.options = init.options;
    this.sandbox = init.sandbox;
  }

  /**
   * The conversion of a JSON filter pandoc is running: the output format
   * is the first argument, the rest is in the environment
   * (`PANDOC_SANDBOX`: `"true"` or `"false"`; anything else is unknown).
   */
  static fromEnvironment(args: readonly string[], env: Record<string, string | undefined>): Conversion {
    const reader = env["PANDOC_READER_OPTIONS"];
    const writer = env["PANDOC_WRITER_OPTIONS"];
    const sandbox = env["PANDOC_SANDBOX"];
    return new Conversion({
      format: args[0],
      inputFormat: env["PANDOC_INPUT_FORMAT"] || undefined,
      outputFormat: env["PANDOC_OUTPUT_FORMAT"] || undefined,
      readerOptions: reader ? (JSON.parse(reader) as Record<string, unknown>) : undefined,
      writerOptions: writer ? (JSON.parse(writer) as Record<string, unknown>) : undefined,
      sandbox: sandbox === "true" ? true : sandbox === "false" ? false : undefined,
    });
  }

  /**
   * The conversion libpandoc describes to a filter it calls in process
   * (a JS filter function's second argument): its context, `"format"`,
   * `"input-format"`, `"output-format"`, `"reader-options"`,
   * `"writer-options"` (from libpandoc 1.10) and `"sandbox"` (1.13); and,
   * if the host knows them, the conversion's `options` (defaults-file
   * keys), which the context doesn't carry. What the context lacks is
   * `undefined`.
   */
  static fromContext(context: Record<string, unknown>, options?: Record<string, unknown>): Conversion {
    const text = (k: string) => {
      const v = context[k];
      return typeof v === "string" && v ? v : undefined;
    };
    const object = (k: string) => {
      const v = context[k];
      return v !== null && typeof v === "object" && !Array.isArray(v) ? (v as Record<string, unknown>) : undefined;
    };
    const sandbox = context["sandbox"];
    return new Conversion({
      format: text("format"),
      inputFormat: text("input-format"),
      outputFormat: text("output-format"),
      readerOptions: object("reader-options"),
      writerOptions: object("writer-options"),
      options,
      sandbox: typeof sandbox === "boolean" ? sandbox : undefined,
    });
  }
}
