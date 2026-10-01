interface ParamInputsProps {
  /** The saved params: their keys are the fields, their value types decide the validation. */
  params: Record<string, unknown>;
  /** What each field currently says (`rawFromParams` to start). */
  raw: Record<string, string>;
  /** The catalog's allowed names per enum param (Story 27.7): those params get a dropdown. */
  choices: Record<string, string[]>;
  /** Param keys `invalidParamKeys` refused: flagged `aria-invalid`, with one alert naming them. */
  invalidKeys: string[];
  onEdit: (key: string, value: string) => void;
}

/**
 * The one param editor (Story 32.3, SSOT-02): a text input per param, a `<select>` for a catalog
 * `choices` param. The chart's settings modal and the Technicals tab's row render this and keep
 * their own draft state (`paramCoercion.ts` holds the draft rules), so there is exactly one copy
 * of the input logic. Invalid text is shown, not silently reverted.
 */
export default function ParamInputs({ params, raw, choices, invalidKeys, onEdit }: ParamInputsProps) {
  return (
    <>
      {Object.keys(params).map((key) => (
        <label key={key}>
          {key}:
          {choices[key] ? (
            <select
              value={raw[key] ?? ""}
              aria-invalid={invalidKeys.includes(key)}
              onChange={(e) => onEdit(key, e.target.value)}
            >
              {/* A saved value the catalog no longer offers stays visible (and invalid), never
                  silently replaced by the first option. */}
              {!choices[key].includes(raw[key] ?? "") && <option value={raw[key] ?? ""}>{raw[key]}</option>}
              {choices[key].map((choice) => (
                <option key={choice} value={choice}>
                  {choice}
                </option>
              ))}
            </select>
          ) : (
            <input
              value={raw[key] ?? ""}
              aria-invalid={invalidKeys.includes(key)}
              onChange={(e) => onEdit(key, e.target.value)}
            />
          )}
        </label>
      ))}
      {invalidKeys.length > 0 && <span role="alert">Invalid value for {invalidKeys.join(", ")}</span>}
    </>
  );
}
