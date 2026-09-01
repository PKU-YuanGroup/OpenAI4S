import { describe, expect, it } from "vitest";
import { annotationId, annotationIsHeld, annotationStatus, openAnnotations } from "./annot";
import { annotations } from "../stores/session";
import { resetStoreFields } from "../stores/signal-field";

describe("annotationStatus (app.js:9244-9253)", () => {
  it("maps reserved/pending to pending and unknown rather than open", () => {
    expect(annotationStatus({ status: "open" })).toBe("open");
    expect(annotationStatus({ status: "sent" })).toBe("sent");
    expect(annotationStatus({ status: "resolved" })).toBe("resolved");
    expect(annotationStatus({ status: "dismissed" })).toBe("dismissed");
    expect(annotationStatus({ status: "reserved" })).toBe("pending");
    expect(annotationStatus({ status: "pending" })).toBe("pending");
    expect(annotationStatus({ status: "weird" })).toBe("unknown");
    expect(annotationStatus({})).toBe("open");
  });

  it("holds pending and unknown pins so the user cannot delete them", () => {
    expect(annotationIsHeld({ status: "pending" })).toBe(true);
    expect(annotationIsHeld({ status: "reserved" })).toBe(true);
    expect(annotationIsHeld({ status: "weird" })).toBe(true);
    expect(annotationIsHeld({ status: "open" })).toBe(false);
    expect(annotationIsHeld({ status: "sent" })).toBe(false);
  });

  it("annotationId prefers id then annotation_id", () => {
    expect(annotationId({ id: "a", annotation_id: "b" })).toBe("a");
    expect(annotationId({ annotation_id: "b" })).toBe("b");
    expect(annotationId(null)).toBeFalsy();
  });
});

describe("openAnnotations (app.js:8966)", () => {
  it("filters status===open from the session store", () => {
    resetStoreFields();
    annotations.value = [
      { id: "1", status: "open" },
      { id: "2", status: "sent" },
      { id: "3", status: "pending" },
    ];
    expect(openAnnotations().map((a) => a.id)).toEqual(["1"]);
    resetStoreFields();
  });
});
