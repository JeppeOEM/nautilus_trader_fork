import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { DEFAULT_VOLUME_PROFILE_SETTINGS } from "../../lib/volumeProfile";
import VolumeProfileSettingsPanel from "./VolumeProfileSettings";

afterEach(cleanup);

describe("VolumeProfileSettingsPanel (Story 18.5)", () => {
  const setup = () => {
    const onChange = vi.fn();
    render(<VolumeProfileSettingsPanel value={DEFAULT_VOLUME_PROFILE_SETTINGS} onChange={onChange} />);
    return onChange;
  };

  it("reports a row-count change, rounded and clamped to 1..500", () => {
    const onChange = setup();
    const input = screen.getByLabelText("Row count");

    fireEvent.change(input, { target: { value: "48.6" } });
    fireEvent.change(input, { target: { value: "9999" } });
    fireEvent.change(input, { target: { value: "0" } });

    expect(onChange.mock.calls.map((c) => c[0].rowCount)).toEqual([49, 500, 1]);
  });

  it("clamps value area % to 1..100 and ignores an emptied field", () => {
    const onChange = setup();
    const input = screen.getByLabelText("Value area percent");

    fireEvent.change(input, { target: { value: "250" } });
    fireEvent.change(input, { target: { value: "" } });

    expect(onChange).toHaveBeenCalledTimes(1);
    expect(onChange.mock.calls[0][0].valueAreaPercent).toBe(100);
  });

  it("does not snap a momentarily empty field back while typing", () => {
    setup();
    const input = screen.getByLabelText("Row count") as HTMLInputElement;

    fireEvent.change(input, { target: { value: "" } });

    expect(input.value).toBe("");
  });

  it("reports colors and the POC / value-area toggles, leaving other fields intact", () => {
    const onChange = setup();

    fireEvent.change(screen.getByLabelText("Up volume color"), { target: { value: "#123456" } });
    fireEvent.click(screen.getByLabelText("Show POC"));
    fireEvent.click(screen.getByLabelText("Show value area"));

    expect(onChange.mock.calls[0][0]).toEqual({ ...DEFAULT_VOLUME_PROFILE_SETTINGS, upColor: "#123456" });
    expect(onChange.mock.calls[1][0].showPoc).toBe(false);
    expect(onChange.mock.calls[2][0].showValueArea).toBe(false);
  });
});
