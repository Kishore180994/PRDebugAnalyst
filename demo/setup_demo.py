#!/usr/bin/env python3
"""
Sets up realistic demo data for PR Debug Analyst walkthrough.
Creates:
  - A fake Tasks folder dump with nested logs for many PRs
  - A fake Android project with build files
  - A Terminal A log file simulating build output
"""
import os
import random

DEMO_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_data")
TASKS_FOLDER = os.path.join(DEMO_ROOT, "tasks_dump")
PROJECT_FOLDER = os.path.join(DEMO_ROOT, "android_project")
TERMINAL_A_LOG = os.path.join(DEMO_ROOT, "terminal_a.log")


def create_tasks_dump():
    """Create a realistic tasks dump with lots of noise and a few target PR logs."""

    structures = {
        # ── Organized CI output folders ──────────────────────
        "ci-builds/2026-03-10/job-44201/console.log": LOG_PR_892_UNRELATED,
        "ci-builds/2026-03-10/job-44202/console.log": LOG_PR_1147_TARGET_FAIL_1,
        "ci-builds/2026-03-12/job-44389/console.log": LOG_PR_1200_UNRELATED,
        "ci-builds/2026-03-12/job-44390/build_output.txt": LOG_PR_1147_TARGET_FAIL_2,
        "ci-builds/2026-03-14/job-44501/console.log": LOG_PR_1300_UNRELATED,

        # ── Flat dump of random logs ─────────────────────────
        "raw_logs/build_20260310_142301.log": LOG_NOISE_GRADLE_SUCCESS,
        "raw_logs/build_20260311_091500.log": LOG_PR_999_UNRELATED,
        "raw_logs/build_20260312_153022.log": LOG_PR_1147_TARGET_FAIL_3,
        "raw_logs/error_dump_march.txt": LOG_NOISE_MIXED_ERRORS,

        # ── Team export folder ───────────────────────────────
        "team_exports/alice/pr-1147-investigation.txt": LOG_PR_1147_NOTES,
        "team_exports/bob/random_debug_notes.txt": LOG_NOISE_RANDOM_NOTES,
        "team_exports/bob/pr_1050_fixed.log": LOG_PR_1050_UNRELATED,

        # ── Deeply nested ────────────────────────────────────
        "archive/2026/Q1/march/week2/build-failures/1147.log": LOG_PR_1147_TARGET_FAIL_4,
        "archive/2026/Q1/march/week2/build-failures/1200.log": LOG_PR_1200_UNRELATED,
        "archive/2026/Q1/march/week1/build-failures/998.log": LOG_PR_998_UNRELATED,

        # ── Noise: non-log files ─────────────────────────────
        "docs/build-process.md": NOISE_MARKDOWN,
        "scripts/cleanup.sh": NOISE_SHELL_SCRIPT,
        "configs/ci-config.yaml": NOISE_YAML,
    }

    for rel_path, content in structures.items():
        full_path = os.path.join(TASKS_FOLDER, rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w") as f:
            f.write(content)

    print(f"  Created {len(structures)} files in tasks dump")


def create_android_project():
    """Create a minimal fake Android project."""
    files = {
        "build.gradle": PROJECT_ROOT_GRADLE,
        "settings.gradle": PROJECT_SETTINGS_GRADLE,
        "gradle.properties": PROJECT_GRADLE_PROPERTIES,
        "gradle/wrapper/gradle-wrapper.properties": GRADLE_WRAPPER_PROPS,
        "app/build.gradle": PROJECT_APP_GRADLE,
        "app/src/main/AndroidManifest.xml": ANDROID_MANIFEST,
        "app/src/main/java/com/example/myapp/MainActivity.kt": MAIN_ACTIVITY,
        "library/build.gradle": LIBRARY_GRADLE,
    }

    for rel_path, content in files.items():
        full_path = os.path.join(PROJECT_FOLDER, rel_path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w") as f:
            f.write(content)

    print(f"  Created {len(files)} project files")


def create_terminal_a_log():
    """Create a simulated Terminal A output log (as if user ran a gradle build)."""
    with open(TERMINAL_A_LOG, "w") as f:
        f.write(TERMINAL_A_BUILD_OUTPUT)
    print(f"  Created Terminal A log")


# ═══════════════════════════════════════════════════════════════════════════
#  LOG CONTENT TEMPLATES
# ═══════════════════════════════════════════════════════════════════════════

# ── TARGET PR: 1147 (the one we're debugging) ────────────────────────────

LOG_PR_1147_TARGET_FAIL_1 = """=== CI Build Log ===
Job: #44202
PR: https://github.com/AcmeCorp/SuperApp/pull/1147
Branch: feature/add-payment-gateway
Author: dev@acmecorp.com
Triggered: 2026-03-10T14:23:01Z

> Configure project :app
> Configure project :library

> Task :library:compileDebugKotlin
w: /src/library/src/main/java/com/example/library/PaymentSDK.kt: (12, 5): Parameter 'context' is never used

> Task :app:processDebugResources

> Task :app:compileDebugKotlin FAILED

FAILURE: Build failed with an exception.

* What went wrong:
Execution failed for task ':app:compileDebugKotlin'.
> A failure occurred while executing org.jetbrains.kotlin.compilerRunner.GradleCompilerRunnerWithWorkers$GradleKotlinCompilerWorkAction
   > Compilation error. See log for more details

e: /src/app/src/main/java/com/example/myapp/PaymentActivity.kt: (45, 12): Unresolved reference: PaymentConfig
e: /src/app/src/main/java/com/example/myapp/PaymentActivity.kt: (52, 8): Unresolved reference: PaymentConfig

* Try:
> Run with --stacktrace option to get the stack trace.
> Run with --info or --debug option to get more log output.

BUILD FAILED in 2m 34s
47 actionable tasks: 32 executed, 15 up-to-date
"""

LOG_PR_1147_TARGET_FAIL_2 = """CI Pipeline - Build #44390
Repository: AcmeCorp/SuperApp
Pull Request: #1147 (feature/add-payment-gateway)
Stage: build-debug
Runner: linux-runner-03

Downloading artifacts from previous stage...
Setting ANDROID_HOME=/opt/android-sdk
Setting JAVA_HOME=/usr/lib/jvm/java-17

Starting Gradle build...
Downloading https://services.gradle.org/distributions/gradle-8.5-bin.zip
.........10%.........20%.........30%.........40%.........50%.........60%.........70%.........80%.........90%.........100%

> Task :buildSrc:compileKotlin UP-TO-DATE
> Task :buildSrc:compileJava NO-SOURCE
> Task :buildSrc:jar UP-TO-DATE

> Task :library:compileDebugKotlin
> Task :library:javaPreCompileDebug
> Task :library:compileDebugJavaWithJavac NO-SOURCE

> Task :app:kaptGenerateStubsDebugKotlin
> Task :app:kaptDebugKotlin
> Task :app:compileDebugKotlin FAILED

FAILURE: Build failed with an exception.

* What went wrong:
Execution failed for task ':app:compileDebugKotlin'.
> A failure occurred while executing org.jetbrains.kotlin.compilerRunner.GradleCompilerRunnerWithWorkers$GradleKotlinCompilerWorkAction
   > Compilation error. See log for more details

e: file:///src/app/src/main/java/com/example/myapp/PaymentActivity.kt: (45, 12): Unresolved reference: PaymentConfig
e: file:///src/app/src/main/java/com/example/myapp/PaymentActivity.kt: (52, 8): Unresolved reference: PaymentConfig
e: file:///src/app/src/main/java/com/example/myapp/CheckoutFragment.kt: (23, 15): Type mismatch: inferred type is PaymentSDK but PaymentSDK? was expected

* Try:
> Run with --stacktrace option to get the stack trace.

BUILD FAILED in 3m 12s
"""

LOG_PR_1147_TARGET_FAIL_3 = """build_20260312_153022 | SuperApp Debug Build
PR-1147 | branch: feature/add-payment-gateway
---
Gradle 8.5 / AGP 8.2.1 / Kotlin 1.9.22

:app:compileDebugKotlin FAILED

Errors:
  PaymentActivity.kt:45 - Unresolved reference: PaymentConfig
  PaymentActivity.kt:52 - Unresolved reference: PaymentConfig
  CheckoutFragment.kt:23 - Type mismatch: PaymentSDK vs PaymentSDK?

Root module :library compiled OK.
Issue appears to be missing class PaymentConfig in the library module.
The class was added in the PR but the library module's build.gradle doesn't
expose the :payment-config module as an api() dependency.

BUILD FAILED in 2m 48s
"""

LOG_PR_1147_TARGET_FAIL_4 = """Build Failure Report
====================
PR: 1147
Repo: AcmeCorp/SuperApp
Date: 2026-03-14
Retry: 3 of 3

Same failure as previous attempts.
:app:compileDebugKotlin fails due to unresolved PaymentConfig class.

The dependency chain is:
  :app -> :library -> :payment-config (MISSING dependency declaration)

library/build.gradle has:
  implementation project(':payment-config')
but should be:
  api project(':payment-config')

This causes PaymentConfig to not be visible to :app module.

VERDICT: Build config fix needed in library/build.gradle
Change implementation -> api for :payment-config dependency.
"""

LOG_PR_1147_NOTES = """Investigation notes for PR #1147
================================
Alice's notes - 2026-03-11

Looked at PR https://github.com/AcmeCorp/SuperApp/pull/1147
The payment gateway feature adds a new :payment-config module.

Build fails because :library depends on :payment-config with `implementation`
but :app needs types from :payment-config transitively through :library.

Fix: change library/build.gradle dependency from implementation to api.

Haven't pushed the fix yet because need to verify it doesn't break
the modularization rules we set up.
"""

# ── UNRELATED PR LOGS (noise) ────────────────────────────────────────────

LOG_PR_892_UNRELATED = """=== CI Build Log ===
Job: #44201
PR: https://github.com/AcmeCorp/SuperApp/pull/892
Branch: bugfix/crash-on-login

> Task :app:compileDebugKotlin
> Task :app:assembleDebug

BUILD SUCCESSFUL in 1m 45s
"""

LOG_PR_1200_UNRELATED = """CI Build - PR #1200
Branch: feature/dark-mode
FAILURE: Build failed with an exception.
Execution failed for task ':app:mergeDebugResources'
Manifest merger failed: uses-sdk:minSdkVersion 21 cannot be smaller than 24
BUILD FAILED in 1m 02s
"""

LOG_PR_1300_UNRELATED = """Job #44501 | PR https://github.com/AcmeCorp/SuperApp/pull/1300
Branch: refactor/di-modules
> Task :app:kaptDebugKotlin FAILED
error: [Dagger/MissingBinding] com.example.myapp.UserRepository cannot be provided
BUILD FAILED in 2m 15s
"""

LOG_PR_999_UNRELATED = """Build log for PR-999
Branch: feature/notifications
> Task :app:assembleDebug
BUILD SUCCESSFUL in 3m 01s
All tests passed.
"""

LOG_PR_1050_UNRELATED = """PR #1050 - Fixed!
Was failing due to duplicate class in dependencies.
Fixed by excluding org.jetbrains:annotations from retrofit dependency.
BUILD SUCCESSFUL after fix.
"""

LOG_PR_998_UNRELATED = """Build #998
Execution failed for task ':app:lintDebug'
Lint found errors in the project
BUILD FAILED
"""

# ── NOISE FILES ──────────────────────────────────────────────────────────

LOG_NOISE_GRADLE_SUCCESS = """Starting Gradle Daemon...
Gradle Daemon started in 2s
> Configure project :app
> Task :app:preBuild UP-TO-DATE
> Task :app:compileDebugKotlin
> Task :app:assembleDebug

BUILD SUCCESSFUL in 1m 22s
45 actionable tasks: 10 executed, 35 up-to-date
"""

LOG_NOISE_MIXED_ERRORS = """March Error Dump - Various builds
================================

Build 1 (PR #800): PASSED
Build 2 (PR #801): FAILED - OOM
Build 3 (PR #802): PASSED
Build 4 (PR #810): FAILED - lint errors
Build 5 (PR #850): PASSED
... (truncated)
"""

LOG_NOISE_RANDOM_NOTES = """Bob's debug notes
- Need to update gradle wrapper to 8.6
- Look into KSP migration from KAPT
- Check flaky test: LoginActivityTest
"""

NOISE_MARKDOWN = """# Build Process Documentation
Our CI pipeline runs on every PR push.
Stages: lint -> build -> test -> deploy-preview
"""

NOISE_SHELL_SCRIPT = """#!/bin/bash
# Cleanup old build artifacts
find . -name "*.class" -delete
find . -name "build" -type d -exec rm -rf {} +
"""

NOISE_YAML = """stages:
  - lint
  - build
  - test
variables:
  ANDROID_SDK: "/opt/android-sdk"
"""

# ── ANDROID PROJECT TEMPLATES ────────────────────────────────────────────

PROJECT_ROOT_GRADLE = """// Top-level build file
plugins {
    id 'com.android.application' version '8.2.1' apply false
    id 'com.android.library' version '8.2.1' apply false
    id 'org.jetbrains.kotlin.android' version '1.9.22' apply false
}

task clean(type: Delete) {
    delete rootProject.buildDir
}
"""

PROJECT_SETTINGS_GRADLE = """pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "SuperApp"
include ':app'
include ':library'
include ':payment-config'
"""

PROJECT_GRADLE_PROPERTIES = """org.gradle.jvmargs=-Xmx2048m -Dfile.encoding=UTF-8
android.useAndroidX=true
kotlin.code.style=official
android.nonTransitiveRClass=true
"""

GRADLE_WRAPPER_PROPS = """distributionBase=GRADLE_USER_HOME
distributionPath=wrapper/dists
distributionUrl=https\\://services.gradle.org/distributions/gradle-8.5-bin.zip
networkTimeout=10000
validateDistributionUrl=true
zipStoreBase=GRADLE_USER_HOME
zipStorePath=wrapper/dists
"""

PROJECT_APP_GRADLE = """plugins {
    id 'com.android.application'
    id 'org.jetbrains.kotlin.android'
}

android {
    namespace 'com.example.myapp'
    compileSdk 34

    defaultConfig {
        applicationId "com.example.myapp"
        minSdk 24
        targetSdk 34
        versionCode 1
        versionName "1.0"
    }

    buildTypes {
        release {
            minifyEnabled false
            proguardFiles getDefaultProguardFile('proguard-android-optimize.txt'), 'proguard-rules.pro'
        }
    }
    compileOptions {
        sourceCompatibility JavaVersion.VERSION_17
        targetCompatibility JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = '17'
    }
}

dependencies {
    implementation project(':library')
    implementation 'androidx.core:core-ktx:1.12.0'
    implementation 'androidx.appcompat:appcompat:1.6.1'
    implementation 'com.google.android.material:material:1.11.0'
}
"""

LIBRARY_GRADLE = """plugins {
    id 'com.android.library'
    id 'org.jetbrains.kotlin.android'
}

android {
    namespace 'com.example.library'
    compileSdk 34

    defaultConfig {
        minSdk 24
    }
    compileOptions {
        sourceCompatibility JavaVersion.VERSION_17
        targetCompatibility JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = '17'
    }
}

dependencies {
    // BUG: should be api() so :app can see PaymentConfig transitively
    implementation project(':payment-config')
    implementation 'androidx.core:core-ktx:1.12.0'
}
"""

ANDROID_MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">
    <application
        android:allowBackup="true"
        android:label="SuperApp"
        android:theme="@style/Theme.Material3.DayNight">
        <activity
            android:name=".MainActivity"
            android:exported="true">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>
</manifest>
"""

MAIN_ACTIVITY = """package com.example.myapp

import android.os.Bundle
import androidx.appcompat.app.AppCompatActivity

class MainActivity : AppCompatActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
    }
}
"""

# ── TERMINAL A BUILD OUTPUT (simulated) ──────────────────────────────────

TERMINAL_A_BUILD_OUTPUT = """$ ./gradlew assembleDebug --stacktrace

> Configure project :app
> Configure project :library
> Configure project :payment-config

> Task :payment-config:preBuild UP-TO-DATE
> Task :payment-config:compileDebugKotlin
> Task :library:preBuild UP-TO-DATE
> Task :library:compileDebugKotlin
w: /home/dev/SuperApp/library/src/main/java/com/example/library/PaymentSDK.kt: (12, 5): Parameter 'context' is never used
> Task :app:preBuild UP-TO-DATE
> Task :app:preDebugBuild UP-TO-DATE
> Task :app:generateDebugBuildConfig UP-TO-DATE
> Task :app:processDebugResources UP-TO-DATE
> Task :app:compileDebugKotlin FAILED

FAILURE: Build failed with an exception.

* What went wrong:
Execution failed for task ':app:compileDebugKotlin'.
> A failure occurred while executing org.jetbrains.kotlin.compilerRunner.GradleCompilerRunnerWithWorkers$GradleKotlinCompilerWorkAction
   > Compilation error. See log for more details

e: file:///home/dev/SuperApp/app/src/main/java/com/example/myapp/PaymentActivity.kt: (45, 12): Unresolved reference: PaymentConfig
e: file:///home/dev/SuperApp/app/src/main/java/com/example/myapp/PaymentActivity.kt: (52, 8): Unresolved reference: PaymentConfig

* Exception is:
org.gradle.api.tasks.TaskExecutionException: Execution failed for task ':app:compileDebugKotlin'.
    at org.gradle.api.internal.tasks.execution.ExecuteActionsTaskExecuter.lambda$executeIfValid$1(ExecuteActionsTaskExecuter.java:149)
    at org.gradle.internal.Try$Failure.ifSuccessfulOrElse(Try.java:282)
    ... 45 more
Caused by: org.jetbrains.kotlin.gradle.internal.CompilationErrorException: Compilation error. See log for more details
    at org.jetbrains.kotlin.compilerRunner.GradleCompilerRunnerWithWorkers$GradleKotlinCompilerWorkAction.execute(GradleKotlinCompilerWorkAction.kt:76)
    ... 30 more

* Try:
> Run with --info or --debug option to get more log output.
> Run with --scan to get full insights.

BUILD FAILED in 2m 34s
47 actionable tasks: 32 executed, 15 up-to-date
"""


def main():
    print("\n  Setting up PR Debug Analyst demo data...\n")

    os.makedirs(DEMO_ROOT, exist_ok=True)

    print("  [1/3] Creating tasks dump (20+ files, nested folders)...")
    create_tasks_dump()

    print("  [2/3] Creating fake Android project...")
    create_android_project()

    print("  [3/3] Creating Terminal A log...")
    create_terminal_a_log()

    print(f"""
  ✅ Demo data ready!

  Tasks folder:    {TASKS_FOLDER}
  Project folder:  {PROJECT_FOLDER}
  Terminal A log:  {TERMINAL_A_LOG}

  To run the demo:
    cd {os.path.dirname(os.path.dirname(os.path.abspath(__file__)))}
    export GEMINI_API_KEY="your-key-here"
    python main.py

  When prompted, enter:
    Tasks folder:  {TASKS_FOLDER}
    PR link:       https://github.com/AcmeCorp/SuperApp/pull/1147
    Project path:  {PROJECT_FOLDER}
""")


if __name__ == "__main__":
    main()
