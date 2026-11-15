/**
 * The pandoc conversion a filter runs in, as panir's Python `Conversion`.
 *
 * A filter function finds it in `ctx.conversion`: the output format's name
 * (`format`, what pandoc passes a JSON filter), and, when known, the input
 * and output formats with extensions, the reader's and the writer's options
 * and the conversion's options (defaults-file keys). Who runs the filter
 * decides how much is known:
 *
 * - as a JSON filter under pandoc (`runFilter`): the output format's name
 *   and the reader's options (`$PANDOC_READER_OPTIONS`); the formats with
 *   extensions too once pandoc sets `$PANDOC_INPUT_FORMAT` and
 *   `$PANDOC_OUTPUT_FORMAT` (proposed: jgm/pandoc#11016), as libpandoc
 *   already does, and the writer's options once something sets
 *   `$PANDOC_WRITER_OPTIONS` (proposed to pandoc and libpandoc);
 * - in process, with libpandoc: everything its filter context has.
 */
export interface ConversionInit {
  format?: string;
  inputFormat?: string;
  outputFormat?: string;
  readerOptions?: Record<string, unknown>;
  writerOptions?: Record<string, unknown>;
  options?: Record<string, unknown>;
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

  constructor(init: ConversionInit = {}) {
    this.format = init.format;
    this.inputFormat = init.inputFormat;
    this.outputFormat = init.outputFormat;
    this.readerOptions = init.readerOptions;
    this.writerOptions = init.writerOptions;
    this.options = init.options;
  }

  /**
   * The conversion of a JSON filter pandoc is running: the output format
   * is the first argument, the rest is in the environment.
   */
  static fromEnvironment(args: readonly string[], env: Record<string, string | undefined>): Conversion {
    const reader = env["PANDOC_READER_OPTIONS"];
    const writer = env["PANDOC_WRITER_OPTIONS"];
    return new Conversion({
      format: args[0],
      inputFormat: env["PANDOC_INPUT_FORMAT"] || undefined,
      outputFormat: env["PANDOC_OUTPUT_FORMAT"] || undefined,
      readerOptions: reader ? (JSON.parse(reader) as Record<string, unknown>) : undefined,
      writerOptions: writer ? (JSON.parse(writer) as Record<string, unknown>) : undefined,
    });
  }
}
