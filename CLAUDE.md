# PC Meeting Scheduler

## Project Overview

The project takes into account the papers that need to be discussed and the reviewers' scheduling
preferences to create a schedule for the PC meeting.

## Data Sources

The tool has three main inputs:
* Reviewer assignments: A JSON file with the paper submissions, tags, and reviewer assignments. In
  HotCRP, download "JSON for reviewqualitycollector.org"
* Scheduling preferences: A spreadsheet of reviewers' scheduling preferences, e.g., from Xoyondo.
* Configuration YAML or JSON: Tags for filtering out papers, etc.

## Project Structure

* `src/`: The source code directory.
* `src/main.py`: The main source file for the tool.
* `test/`: Data for testing.
* `pc-meeting-scheduler`: The main script to run the tool.
