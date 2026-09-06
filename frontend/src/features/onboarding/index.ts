export { bootOnboarding } from "./boot";
export { ot } from "./copy";
export {
  capabilityBadgeMarkup,
  capabilityBadgeRows,
  capabilityBadgeText,
  capabilityUnknownReason,
  badgesFromProbe,
  readCapabilityReceipt,
} from "./badges";
export type { BadgeCap, BadgeRow } from "./badges";
export {
  INITIAL_WIZARD,
  REQUIRED_STEPS,
  checklistItems,
  formatWizardError,
  reduceWizard,
  requiredStepCount,
  wizardErrorFromUnknown,
} from "./machine";
export type {
  PathChoice,
  RequiredStep,
  WizardAction,
  WizardError,
  WizardReceipt,
  WizardState,
} from "./machine";
export { sanitizeOnboardingStatus } from "./status";
export type { OnboardingStatus } from "./status";
export { fetchOnboarding, completeOnboarding } from "./api";
