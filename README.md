# Finding Localized Pages That May Need Review

This project explores a simple way to find localized Kubernetes documentation that may no longer match the current English content.

It is designed as a **review helper** for localization teams.

## Why this matters

Work on Kubernetes website localization is not only about translating text.

A large part of the work is checking whether localized pages are still in step with upstream English documentation, deciding what needs review, and choosing what to update first. Different localization teams have developed different ways to handle that work.

Some teams rely heavily on Git history to track upstream changes. One way to control the issue is to avoid partial updates because small local edits can make a page look more current than it really is. Another deprecated practice was to use branch-based workflows to batch updates in a more controlled way. These approaches are all useful, but they do not solve the same problem in the same way. In practice, it can still be hard to tell which localized pages really need attention first. 

## The problem with using Git history alone

Git history is helpful because it is easy to inspect and already part of the normal workflow.

But Git history mainly tells us **that something changed**. It does not always clearly tell us **whether the localized page is still meaningfully aligned with the current English page**. 

For example:

- a small cleanup in a localized page can make the history look recent, even if the page is still missing important upstream updates
- a small formatting or maintenance change in English can create extra work for every localization team, even when no real translation update is needed
- one upstream change can affect many localizations, which can lead to many separate review decisions and many separate PRs 

This is especially important for Korean localization.

Among the current localizations, Korean is one of the clearest examples of how Git-based tracking can become hard to interpret. Korean is one of the most affected locales for this kind of Git-history problem, which makes it a useful case for improving review signals. 

## What this prototype does

This prototype tries a different approach.

Instead of relying mainly on timestamps or commit order, it compares the **current English page** with the **current localized page** and looks for visible differences that often matter during review. Rather than comparing text directly across languages, it compares page structure within matching sections. Examples include headings, lists, code blocks, anchors, shortcodes, and version mentions. 

The goal is not to judge translation quality or to compare meaning word by word. The goal is to provide a lightweight signal that helps maintainers notice pages that are more likely to need review. 

## Simple workflow

The core script follows a simple review flow:

1. It takes an English page and its localized version.
2. It checks a small set of visible content indicators, such as missing sections, missing code examples, missing anchors, or newer version references that appear in English but not in the localized page.
3. It combines those indicators into a simple review bucket, so maintainers can quickly see which files are more likely to need attention first.

In other words, the script is not trying to fully understand meaning across languages. It is trying to answer a practical question: **which pages should we review first?** 

## What this is useful for

This tool is meant to help localization maintainers answer practical questions such as:

- Which files are more likely to need review?
- Which areas should we look at first?
- Which alerts seem important, and which ones are probably low-impact?
- How can we get a quick status view for planning or reporting? 

In that sense, this is mainly a **triage and visibility tool**. It helps teams focus their time where it is most likely to matter. 

## What makes this approach helpful

This approach is useful in two important ways.

First, it can catch cases where the page history looks recent, but the localized content still appears to be behind the current English page. Second, it can reduce noise from changes that look important in Git history but do not actually create much translation work. 

That matters because different localizations work differently. Lightweight content-based indicators can still be helpful across those different workflows. It does not replace local practices. It complements them. 

## In one sentence

This prototype helps localization teams find pages that are more likely to need real review by comparing the current English and localized content structure, not just Git history.