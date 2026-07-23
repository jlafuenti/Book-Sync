# Android Device Name Settings Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a user rename their Android device's display identity (currently a fixed
`Build.MANUFACTURER + Build.MODEL` string) from the Account screen, and show that
device attribution in Android's own native History tab (which currently shows none,
unlike the web Session History panel).

**Architecture:** `DeviceIdManager` gains a persisted, overridable `deviceName` —
mirroring the existing `ServerUrlManager` idiom exactly (a synchronously-readable
`@Volatile` cached value plus a separate reactive `Flow` for UI, and a `suspend` setter
that updates both). `AccountViewModel`/`AccountScreen` add a small settings section
using that flow. `PlayerScreen`'s `HistoryTab` gets a small pure helper function,
extracted the same way Task 5 of the #54 branch extracted `SyncConflictHelpersTest`'s
helpers, so it's unit-testable without Compose or a mocking framework.

**Tech Stack:** Kotlin, Jetpack Compose, Hilt, AndroidX DataStore (Preferences), JUnit
(no mocking framework in this project — do not introduce one).

## Global Constraints

- No mocking framework is available in this Android project (JUnit only) — every new
  test must use real objects (real DataStore via `PreferenceDataStoreFactory`, or pure
  functions), matching every existing Android test in this codebase.
- `DeviceIdManager.deviceName` stays a synchronously-readable `String` (not a `Flow`) —
  `BookSyncRepository`'s existing `deviceIdManager.deviceName` reads (already shipped)
  must keep compiling and working unchanged. Reactive UI updates come from a new,
  separate `deviceNameFlow: Flow<String>` property, exactly mirroring how
  `ServerUrlManager` exposes both `currentUrl` (sync) and `serverUrlFlow` (reactive).
- Client-side validation on the Account screen's device-name field: trim whitespace,
  cap at 60 characters, and blank-after-trim is not a valid "Save" (use "Reset to
  default" instead, which clears the override).
- Changing the device name does NOT require an app restart (contrast with the Server
  URL field, which does because Retrofit's baseUrl is fixed at singleton creation).
- No new automated test for `AccountViewModel` or `AccountScreen` — there is no
  existing test for this ViewModel today (heavy dependencies: `BookSyncApi`,
  `TokenManager`, `ServerUrlManager`, `NetworkMonitor`, no mocking framework
  available), so this stays consistent with current project convention. Verified
  manually instead (steps given in Task 2).
- New pure helper functions extracted for testability must be `internal` top-level
  functions (not `private`, which is file-scoped in Kotlin and would be unreachable
  from a separate test file) — mirrors `internal fun parseSyncTimestamp(...)` in
  `BookSyncRepository.kt:1291`, tested by `SyncConflictHelpersTest.kt` in the same
  package with no import needed.
- Run `cd android && ./gradlew :app:testDebugUnitTest` before every commit that
  touches a test file, foreground and synchronous — confirm 0 failures, no
  regressions in `DeviceIdManagerTest`, `ServerUrlManagerTest`,
  `SyncConflictHelpersTest`, `SyncMatcherParityTest`.
- Do not commit/push to the remote without explicit user confirmation the change
  works (local commits on the current branch are fine and expected between tasks).

---

### Task 1: `DeviceIdManager` — persisted, overridable device name

**Files:**
- Modify: `android/app/src/main/java/com/booksync/data/remote/DeviceIdManager.kt`
- Test: `android/app/src/test/java/com/booksync/data/remote/DeviceIdManagerTest.kt`

**Interfaces:**
- Consumes: nothing new (uses the same `DataStore<Preferences>` already injected).
- Produces (used by Task 2):
  - `DeviceIdManager.deviceName: String` (already existed as a fixed val; now a
    mutable, synchronously-readable cached property — signature/type unchanged, so
    `BookSyncRepository`'s existing reads need no changes).
  - `DeviceIdManager.deviceNameFlow: Flow<String>` (new).
  - `suspend fun DeviceIdManager.setDeviceName(name: String?)` (new). Passing `null`
    or a blank/whitespace-only string clears the override and reverts to the
    auto-derived `"${Build.MANUFACTURER} ${Build.MODEL}"`.

- [ ] **Step 1: Write the failing tests**

Open `android/app/src/test/java/com/booksync/data/remote/DeviceIdManagerTest.kt` and
insert the following five test methods immediately before the file's closing `}`
(after the existing `deviceName is non-empty` test):

```kotlin
    @Test
    fun `deviceName falls back to auto-derived value when no override is stored`() {
        val manager = DeviceIdManager(newDataStore())

        assertEquals(
            "${android.os.Build.MANUFACTURER} ${android.os.Build.MODEL}",
            manager.deviceName,
        )
    }

    @Test
    fun `setDeviceName persists an override readable by a second manager instance`() = runBlocking {
        val dataStore = newDataStore()
        val firstManager = DeviceIdManager(dataStore)

        firstManager.setDeviceName("Kitchen Pixel")

        val secondManager = DeviceIdManager(dataStore)
        assertEquals("Kitchen Pixel", secondManager.deviceName)
    }

    @Test
    fun `setDeviceName trims whitespace before persisting`() = runBlocking {
        val manager = DeviceIdManager(newDataStore())

        manager.setDeviceName("  Kitchen Pixel  ")

        assertEquals("Kitchen Pixel", manager.deviceName)
    }

    @Test
    fun `setDeviceName with null clears the override and reverts to auto-derived`() = runBlocking {
        val dataStore = newDataStore()
        val manager = DeviceIdManager(dataStore)
        manager.setDeviceName("Kitchen Pixel")

        manager.setDeviceName(null)

        assertEquals(
            "${android.os.Build.MANUFACTURER} ${android.os.Build.MODEL}",
            manager.deviceName,
        )
        val overrideKey = stringPreferencesKey("device_name_override")
        assertEquals(null, dataStore.data.first()[overrideKey])
    }

    @Test
    fun `setDeviceName with blank string clears the override and reverts to auto-derived`() = runBlocking {
        val dataStore = newDataStore()
        val manager = DeviceIdManager(dataStore)
        manager.setDeviceName("Kitchen Pixel")

        manager.setDeviceName("   ")

        assertEquals(
            "${android.os.Build.MANUFACTURER} ${android.os.Build.MODEL}",
            manager.deviceName,
        )
    }
```

No new imports are needed — `stringPreferencesKey`, `first`, and `runBlocking` are
already imported at the top of this file.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd android && ./gradlew :app:testDebugUnitTest --tests DeviceIdManagerTest`

Expected: **compile failure** — `unresolved reference: setDeviceName` (the method
doesn't exist yet on `DeviceIdManager`). This is the expected RED state.

- [ ] **Step 3: Implement `DeviceIdManager`**

Replace the full contents of
`android/app/src/main/java/com/booksync/data/remote/DeviceIdManager.kt` with:

```kotlin
package com.booksync.data.remote

import android.os.Build
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.runBlocking
import java.util.UUID
import javax.inject.Inject
import javax.inject.Singleton

private val KEY_DEVICE_ID = stringPreferencesKey("device_id")
private val KEY_DEVICE_NAME_OVERRIDE = stringPreferencesKey("device_name_override")

/**
 * Provides a stable per-install device identity used to attribute bookmark/progress
 * writes to a specific device (see issue #54's multi-device conflict resolution
 * contract: writes carry `device_id` + `device_name`, and the server rejects stale
 * writes with HTTP 409).
 */
@Singleton
class DeviceIdManager @Inject constructor(
    private val dataStore: DataStore<Preferences>
) {
    /**
     * Cached device ID. Initialized once at construction from DataStore, generating
     * and persisting a new UUID on first run. Safe to read synchronously from any
     * thread since `@Volatile` guarantees visibility of writes across threads.
     */
    @Volatile
    var deviceId: String = ""
        private set

    /** Auto-derived fallback name, e.g. "Google Pixel 9 Pro XL". Computed once; stable for the process lifetime. */
    private val autoDeviceName: String = "${Build.MANUFACTURER} ${Build.MODEL}"

    /**
     * Cached, user-facing device name: the persisted override if the user has set one
     * via [setDeviceName], otherwise [autoDeviceName]. Synchronously readable like
     * [deviceId] — existing callers (e.g. `BookSyncRepository`) need no changes.
     * See [deviceNameFlow] for a reactive stream (mirrors `ServerUrlManager.serverUrlFlow`).
     */
    @Volatile
    var deviceName: String = autoDeviceName
        private set

    /** Reactive device-name stream for UI, mirroring [ServerUrlManager.serverUrlFlow]. */
    val deviceNameFlow: Flow<String> =
        dataStore.data.map { it[KEY_DEVICE_NAME_OVERRIDE] ?: autoDeviceName }

    init {
        deviceId = runBlocking {
            val stored = dataStore.data.first()[KEY_DEVICE_ID]
            if (stored != null) {
                stored
            } else {
                val generated = UUID.randomUUID().toString()
                dataStore.edit { it[KEY_DEVICE_ID] = generated }
                generated
            }
        }
        deviceName = runBlocking {
            dataStore.data.first()[KEY_DEVICE_NAME_OVERRIDE] ?: autoDeviceName
        }
    }

    /**
     * Sets a user-chosen device name override. Passing `null`, or a blank/whitespace-only
     * string, clears the override and reverts to the auto-derived name.
     */
    suspend fun setDeviceName(name: String?) {
        val trimmed = name?.trim()
        if (trimmed.isNullOrBlank()) {
            deviceName = autoDeviceName
            dataStore.edit { it.remove(KEY_DEVICE_NAME_OVERRIDE) }
        } else {
            deviceName = trimmed
            dataStore.edit { it[KEY_DEVICE_NAME_OVERRIDE] = trimmed }
        }
    }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd android && ./gradlew :app:testDebugUnitTest --tests DeviceIdManagerTest`

Expected: **PASS** — all 11 tests in this class (6 existing + 5 new), 0 failures.

- [ ] **Step 5: Run the full unit test suite to confirm no regressions**

Run: `cd android && ./gradlew :app:testDebugUnitTest`

Expected: **BUILD SUCCESSFUL**, 0 failures (existing `ServerUrlManagerTest`,
`SyncConflictHelpersTest`, `SyncMatcherParityTest` unaffected — this task only
touched `DeviceIdManager.kt`, which nothing else references yet for `deviceName`
beyond the existing plain-`String` reads).

- [ ] **Step 6: Commit**

```bash
git add android/app/src/main/java/com/booksync/data/remote/DeviceIdManager.kt \
        android/app/src/test/java/com/booksync/data/remote/DeviceIdManagerTest.kt
git commit -m "Android: persisted, overridable device name in DeviceIdManager"
```

---

### Task 2: Account screen — editable "Device" section

**Files:**
- Modify: `android/app/src/main/java/com/booksync/ui/account/AccountViewModel.kt`
- Modify: `android/app/src/main/java/com/booksync/ui/account/AccountScreen.kt`

**Interfaces:**
- Consumes: `DeviceIdManager.deviceName: String`, `DeviceIdManager.deviceNameFlow: Flow<String>`,
  `suspend fun DeviceIdManager.setDeviceName(name: String?)` (all from Task 1).
- Produces: `AccountViewModel.deviceName: StateFlow<String>`,
  `AccountViewModel.setDeviceName(name: String)`, `AccountViewModel.resetDeviceName()`
  (no other task consumes these — this is the UI-facing leaf of the feature).

No automated test for this task — see Global Constraints. Verification is a
compile check plus a manual walkthrough (Steps 3–4 below).

- [ ] **Step 1: Wire `DeviceIdManager` into `AccountViewModel`**

In `android/app/src/main/java/com/booksync/ui/account/AccountViewModel.kt`, add the
import (alongside the existing `com.booksync.data.remote.*` imports, e.g. after the
`ServerUrlManager` import on line 14):

```kotlin
import com.booksync.data.remote.DeviceIdManager
```

Add `deviceIdManager` as a new constructor parameter (after `serverUrlManager` on
line 57):

```kotlin
    private val serverUrlManager: ServerUrlManager,
    private val deviceIdManager: DeviceIdManager,
    networkMonitor: NetworkMonitor,
```

Add the following immediately after the existing `saveServerUrlAndRestart` function
(after line 69, before the `isOnline` property):

```kotlin

    val deviceName = deviceIdManager.deviceNameFlow
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000L), deviceIdManager.deviceName)

    fun setDeviceName(name: String) {
        viewModelScope.launch { deviceIdManager.setDeviceName(name) }
    }

    fun resetDeviceName() {
        viewModelScope.launch { deviceIdManager.setDeviceName(null) }
    }
```

- [ ] **Step 2: Add the "Device" section to `AccountScreen`**

In `android/app/src/main/java/com/booksync/ui/account/AccountScreen.kt`, no new
imports are needed — `Column`, `Button`, `OutlinedTextField`, `clickable`,
`remember`/`mutableStateOf`, `KeyboardOptions`, and `ImeAction` are all already
imported (this section reuses the same components as the existing "Server"
section).

Add the `deviceName` collection near the other `collectAsState()` calls (after line
107, `val serverUrl by viewModel.serverUrl.collectAsState()`):

```kotlin
    val deviceName             by viewModel.deviceName.collectAsState()
```

Insert the following new section between the end of the "Storage" section's closing
`}` (line 257, right after `ActionRow("Clear all downloads", ...)`'s enclosing
`Column` closes) and the `item { SectionTitle("Diagnostics") }` line (line 259):

```kotlin
            item { SectionTitle("Device") }
            item {
                var deviceNameEdit by remember(deviceName) { mutableStateOf(deviceName) }
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .clip(Tandem.shapes.card)
                        .background(colors.bgCard)
                        .padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    OutlinedTextField(
                        value = deviceNameEdit,
                        onValueChange = { if (it.length <= 60) deviceNameEdit = it },
                        label = { Text("Device name") },
                        singleLine = true,
                        modifier = Modifier.fillMaxWidth(),
                        keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
                    )
                    Button(
                        onClick = { viewModel.setDeviceName(deviceNameEdit.trim()) },
                        enabled = deviceNameEdit.isNotBlank() && deviceNameEdit.trim() != deviceName,
                        modifier = Modifier.fillMaxWidth().height(44.dp),
                    ) {
                        Text("Save")
                    }
                    Text(
                        "Shown to your other devices in Session History (e.g. \"Kitchen Pixel\").",
                        color = colors.textMuted,
                        fontSize = 12.sp,
                    )
                    Text(
                        "Reset to default",
                        color = colors.accent,
                        fontSize = 13.sp,
                        fontWeight = FontWeight.SemiBold,
                        modifier = Modifier.clickable { viewModel.resetDeviceName() },
                    )
                }
            }

```

This follows the same card/field/button visual pattern as the existing "Server"
section directly below it (lines 296-332 before this edit).

- [ ] **Step 3: Compile-check**

Run: `cd android && ./gradlew :app:assembleDebug`

Expected: **BUILD SUCCESSFUL**. This confirms the new Hilt constructor parameter,
the new Compose code, and the new imports all compile — there is no unit test for
this ViewModel/screen (see Global Constraints), so this is the only automated gate
for this task.

- [ ] **Step 4: Manual verification (you, on a device/emulator)**

This step cannot be automated in this environment — perform it yourself after
installing the debug APK (`adb install -r app/build/outputs/apk/debug/app-debug.apk`):

1. Open the app → Account tab. Confirm a new "Device" section appears between
   "Storage" and "Diagnostics", pre-filled with the current auto-derived name (e.g.
   "Google Pixel 9 Pro XL").
2. Change the text, tap "Save". Confirm the field keeps the new value (no crash,
   no revert).
3. Force-close and reopen the app, return to Account. Confirm the edited name is
   still shown (proves persistence across process restarts).
4. Tap "Reset to default". Confirm the field reverts to the auto-derived name.
5. Play any audiobook briefly (enough to trigger a heartbeat/pause write), then
   check the web app's Session History for that book pair — confirm the entry's
   device attribution now shows your custom name instead of the raw model string.

- [ ] **Step 5: Commit**

```bash
git add android/app/src/main/java/com/booksync/ui/account/AccountViewModel.kt \
        android/app/src/main/java/com/booksync/ui/account/AccountScreen.kt
git commit -m "Android: editable device name in Account screen"
```

---

### Task 3: History tab — device attribution display

**Files:**
- Modify: `android/app/src/main/java/com/booksync/ui/player/PlayerScreen.kt`
- Test: `android/app/src/test/java/com/booksync/ui/player/HistoryDeviceSuffixTest.kt` (new)

**Interfaces:**
- Consumes: `BookmarkLogResponse.device_id: String?`, `BookmarkLogResponse.device_name: String?`
  (both already exist, shipped in the #54 branch — `android/app/src/main/java/com/booksync/data/remote/dto/ApiDtos.kt:212-213`).
- Produces: `internal fun historyDeviceSuffix(deviceName: String?, deviceId: String?): String`
  — not consumed by any other task in this plan, but follows the same `internal`
  top-level pure-function convention as `parseSyncTimestamp` in `BookSyncRepository.kt`
  for future reuse/testability.

- [ ] **Step 1: Write the failing test**

Create `android/app/src/test/java/com/booksync/ui/player/HistoryDeviceSuffixTest.kt`
with these contents:

```kotlin
package com.booksync.ui.player

import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * Unit test for [historyDeviceSuffix], the pure helper that renders device
 * attribution in Android's native History tab (mirrors the web Session History
 * panel's device-name/device-id fallback logic from issue #54's Task 3).
 */
class HistoryDeviceSuffixTest {

    @Test
    fun `uses device name when present`() {
        assertEquals(" · from Kitchen Pixel", historyDeviceSuffix("Kitchen Pixel", "device-abc"))
    }

    @Test
    fun `falls back to device id when name is absent`() {
        assertEquals(" · from device-abc", historyDeviceSuffix(null, "device-abc"))
    }

    @Test
    fun `falls back to device id when name is blank`() {
        assertEquals(" · from device-abc", historyDeviceSuffix("   ", "device-abc"))
    }

    @Test
    fun `renders empty string when both are absent`() {
        assertEquals("", historyDeviceSuffix(null, null))
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd android && ./gradlew :app:testDebugUnitTest --tests HistoryDeviceSuffixTest`

Expected: **compile failure** — `unresolved reference: historyDeviceSuffix` (the
function doesn't exist yet).

- [ ] **Step 3: Implement `historyDeviceSuffix` and wire it into `HistoryTab`**

In `android/app/src/main/java/com/booksync/ui/player/PlayerScreen.kt`, insert this
function immediately after `formatAbsoluteTime` (after line 1348, before the "Plain
curved arrow" comment block):

```kotlin

/**
 * Renders the trailing " · from {label}" suffix for a History-tab entry, preferring
 * a friendly device name over the raw device id, and rendering nothing at all when
 * neither is present (e.g. legacy log rows predating issue #54's device attribution).
 */
internal fun historyDeviceSuffix(deviceName: String?, deviceId: String?): String {
    val label = deviceName?.trim()?.takeIf { it.isNotBlank() } ?: deviceId
    return if (label.isNullOrBlank()) "" else " · from $label"
}
```

Then change the subtitle `Text` inside `HistoryTab` (originally lines 1248-1252) from:

```kotlin
                    Text(
                        text = "${item.source} · ${formatAbsoluteTime(item.changed_at)}",
                        color = colors.textMuted,
                        fontSize = 12.sp,
                    )
```

to:

```kotlin
                    Text(
                        text = "${item.source} · ${formatAbsoluteTime(item.changed_at)}" +
                            historyDeviceSuffix(item.device_name, item.device_id),
                        color = colors.textMuted,
                        fontSize = 12.sp,
                    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd android && ./gradlew :app:testDebugUnitTest --tests HistoryDeviceSuffixTest`

Expected: **PASS** — all 4 tests.

- [ ] **Step 5: Run the full unit test suite to confirm no regressions**

Run: `cd android && ./gradlew :app:testDebugUnitTest`

Expected: **BUILD SUCCESSFUL**, 0 failures.

- [ ] **Step 6: Commit**

```bash
git add android/app/src/main/java/com/booksync/ui/player/PlayerScreen.kt \
        android/app/src/test/java/com/booksync/ui/player/HistoryDeviceSuffixTest.kt
git commit -m "Android: show device attribution in native History tab"
```

---

## Final Verification

1. `cd android && ./gradlew :app:testDebugUnitTest` — all green (existing suite +
   9 new tests: 5 in `DeviceIdManagerTest`, 4 in `HistoryDeviceSuffixTest`).
2. `cd android && ./gradlew :app:assembleDebug` — builds cleanly.
3. Perform Task 2's Step 4 manual walkthrough on a device/emulator (edit name, force
   restart, reset, confirm it reaches the server and shows in web Session History).
4. Do not push until you've confirmed the manual walkthrough works as expected.
