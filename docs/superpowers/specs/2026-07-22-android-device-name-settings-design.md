# Android: editable device name + History-tab device display

## Context

Issue #54's multi-device conflict resolution (already shipped) gave every device a
stable identity (`device_id`, a UUID) and a friendly `device_name`. On Android,
`device_name` is currently a fixed, non-persisted value computed once from
`Build.MANUFACTURER + Build.MODEL` (e.g. `"Google Pixel 9 Pro XL"`) — the code
already anticipated this being made configurable (`DeviceIdManager.kt:35`: *"Not
persisted or overridable (yet)"*).

Verifying the shipped feature against production surfaced two gaps:
1. The auto-derived name is long and not user-chosen — the user wants to shorten
   it to something that reads better in Session History (e.g. "Pixel" instead of
   "Google Pixel 9 Pro XL").
2. The web's Session History panel shows device attribution (Task 3 of the #54
   plan), but Android's own native History tab (`PlayerScreen.kt`'s `HistoryTab`)
   does not — it currently renders only `"{source} · {timestamp}"` with no device
   info, even though `BookmarkLogResponse` has carried `device_id`/`device_name`
   since Task 5.

This spec covers making the device name user-editable from the Account screen, and
bringing Android's History tab to parity with web's device-attribution display.

## Design

### 1. `DeviceIdManager` — persisted, overridable name

`android/app/src/main/java/com/booksync/data/remote/DeviceIdManager.kt`:

- Add a new DataStore key: `stringPreferencesKey("device_name_override")`.
- `deviceName` changes from a fixed `val` to a `Flow<String>` (or an equivalent
  reactive read) resolving to: the stored override if present and non-blank,
  otherwise the existing auto-derived `"${Build.MANUFACTURER} ${Build.MODEL}"`.
- Add `suspend fun setDeviceName(name: String?)`: trims the input; if the result
  is null or blank, clears the override (`dataStore.edit { it.remove(KEY) }`) so
  the auto-derived name takes over again; otherwise persists the trimmed value.
- Mirrors `ServerUrlManager`'s existing mutate-and-persist idiom already used
  elsewhere in this file's sibling classes, so `BookSyncRepository`'s reads of
  `deviceIdManager.deviceName` for outgoing writes keep working — they simply see
  the override once one is set, with no other repository changes needed.

### 2. Account screen — new "Device" section

`android/app/src/main/java/com/booksync/ui/account/AccountScreen.kt` /
`AccountViewModel.kt`:

- New section placed after "Storage" and before "Diagnostics", styled like the
  existing "Server" card (`OutlinedTextField` + button, same enablement pattern:
  the Save button is enabled only when the trimmed value is non-blank and differs
  from the current effective name).
- Shows the current effective device name (override or auto-derived) pre-filled
  in the field.
- A "Reset to default" text action beneath the field clears the override and
  reverts the displayed value to the auto-derived name — calls
  `deviceIdManager.setDeviceName(null)`.
- No app restart required (unlike the Server URL field) — the change just needs
  to be visible to `BookSyncRepository`'s next outgoing write.
- Client-side validation: trim whitespace; cap input at 60 characters (well under
  the server's 200-char column, short enough to read well in Session History);
  blank-after-trim is not a valid "Save" — that's what "Reset to default" is for.
- `AccountViewModel` exposes the current effective name as a `StateFlow<String>`
  (collected the same way `serverUrl`/`appTheme` already are) and a
  `setDeviceName(name: String)` passthrough to `DeviceIdManager`.

### 3. History tab — device display

`android/app/src/main/java/com/booksync/ui/player/PlayerScreen.kt`'s `HistoryTab`
(~line 1249):

- Extract a small pure helper:
  ```kotlin
  fun historyDeviceSuffix(deviceName: String?, deviceId: String?): String {
      val label = deviceName ?: deviceId
      return if (label.isNullOrBlank()) "" else " · from $label"
  }
  ```
- Change the subtitle line from
  `"${item.source} · ${formatAbsoluteTime(item.changed_at)}"` to
  `"${item.source} · ${formatAbsoluteTime(item.changed_at)}${historyDeviceSuffix(item.device_name, item.device_id)}"`.
- `BookmarkLogResponse` already carries `device_id`/`device_name` (added in
  Task 5) — this is a display-only change, no DTO or repository work needed.
- Legacy/pre-migration log rows with both fields null render with no device
  suffix at all (not "from null").

### Error handling / edge cases

- Blank or whitespace-only input to `setDeviceName` is treated as a clear, not a
  literal blank name being persisted (a blank stored override would otherwise
  silently resolve back to the auto-derived name on next read anyway, so this
  just makes that behavior explicit at write time rather than surprising later).
- Overlong input is truncated to 60 chars client-side before persisting — the
  server's 200-char column is never at risk regardless.
- No network call is needed to change the name locally; it only reaches the
  server on the next bookmark/progress write that includes it, exactly as
  `device_id`/`device_name` already flow today.

### Testing

- `DeviceIdManagerTest.kt` (real DataStore, no mocking framework, matching this
  project's existing constraint): `setDeviceName` persists an override readable
  by a second manager instance backed by the same DataStore file; with no
  override, `deviceName` resolves to the auto-derived value; `setDeviceName(null)`
  (and `setDeviceName("   ")`) clears the override and reverts to auto-derived.
- `historyDeviceSuffix` gets a small, dedicated pure-function unit test (same
  spirit as `SyncConflictHelpersTest` from the #54 branch): both fields present →
  uses `device_name`; only `device_id` present → falls back to it; both null →
  empty string.
- No new test for `AccountViewModel`/`AccountScreen` — there is no existing test
  for this ViewModel today (heavy dependencies, no mocking framework available),
  so this stays consistent with current project convention. Verified manually via
  the Account screen and the History tab in a running build instead.

## Out of scope

- No changes to the server or web platforms — `device_name` is already a free-form
  string server-side; nothing about the contract changes.
- No app restart / process-kill flow (contrast with the Server URL field, which
  needs one because it affects network client construction).
- No validation beyond length/blankness (no uniqueness check, no profanity
  filter, etc.) — this is a personal, per-install display label.
