"""
Job Hunter — AI-Powered Multi-Agent Job Search
Launch the web application. The pipeline runs from the browser:
  1. Upload resume → Parse profile
  2. Enter desired role → Refine search context with LLM
  3. Agents scrape, review companies, match & rank jobs
  4. Dashboard displays results with tailored resume/cover letter generation
"""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))


def main():
    print("=" * 60)
    print("  JOB HUNTER — AI-Powered Multi-Agent Job Search")
    print("=" * 60)
    print()
    print("  Open http://localhost:5050 in your browser to start.")
    print("  Press Ctrl+C to stop.")
    print()

    from portal.app import app
    app.run(debug=False, port=5050, host="0.0.0.0")


if __name__ == "__main__":
    main()
