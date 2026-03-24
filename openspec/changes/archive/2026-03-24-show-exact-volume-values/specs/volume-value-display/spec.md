## ADDED Requirements

### Requirement: GUI SHALL display exact effective volume details for the selected scenario
The system SHALL show a read-only volume detail view derived from `noise_gain_db` and `wakeup_gain_db`, so operators can directly confirm the effective noise and wake-word volume settings of the currently selected scenario.

#### Scenario: Single selected scenario shows both dB and derived exact value
- **WHEN** the operator selects one scenario in the GUI
- **THEN** the system SHALL display the selected scenario's noise and wake-word volume details using the configured dB values and their derived exact display values

#### Scenario: Editing gain updates the displayed exact volume value
- **WHEN** the operator changes `noise_gain_db` or `wakeup_gain_db` for the selected scenario
- **THEN** the system SHALL refresh the volume detail display without requiring a reload or a new run

### Requirement: GUI SHALL handle mixed selections without showing misleading single values
When multiple scenarios are selected, the system SHALL avoid presenting one misleading exact volume value if the underlying gain settings differ.

#### Scenario: Multi-select with identical gain values keeps a shared volume display
- **WHEN** the operator selects multiple scenarios whose relevant gain values are identical
- **THEN** the system SHALL display the shared exact volume values for the selection

#### Scenario: Multi-select with different gain values shows a mixed-value state
- **WHEN** the operator selects multiple scenarios whose noise or wake-word gain values differ
- **THEN** the system SHALL show a mixed-value indication instead of a single exact volume number for that field

### Requirement: Precheck SHALL expose the effective volume details that will be used for the run
The system SHALL include human-readable noise and wake-word volume detail lines in precheck output so the operator can verify the effective volume settings before starting a run.

#### Scenario: Precheck prints effective volume details for each enabled scenario
- **WHEN** the operator runs precheck with one or more enabled scenarios
- **THEN** the system SHALL print each enabled scenario's effective noise and wake-word volume details in the precheck output

#### Scenario: Precheck reflects the latest edited gain values
- **WHEN** the operator changes a scenario gain value in the GUI and immediately runs precheck
- **THEN** the precheck output SHALL reflect the updated effective volume details rather than a stale value
