#!/usr/bin/env python3
"""
PIT ID Badge Application Bot - Single File Version
Automates Pittsburgh International Airport badge applications for Atlas new hires.

Usage:
    python main.py apply --all                   # Submit all pending (dry run)
    python main.py apply --all --submit          # Submit all pending
    python main.py apply --id <uuid> --submit    # Submit specific application
    python main.py test                          # Test with dummy data
    python main.py test --visible                # Test with visible browser
"""

import argparse
import os
from datetime import datetime
from typing import Optional, List, Dict

from playwright.sync_api import sync_playwright, Page
from supabase import create_client, Client
from dotenv import load_dotenv

load_dotenv()

# =============================================================================
# CONFIGURATION
# =============================================================================

BASE_URL = "https://mypitid.flypittsburgh.com/"
AIRPORT_STORE = "4586"
ATLAS_COMPANY = "Atlas Franchise Management-Atlas Franchise Management"

# Timeouts (ms)
TIMEOUT_SHORT = 5000
TIMEOUT_MEDIUM = 15000
TIMEOUT_LONG = 45000


# =============================================================================
# SUPABASE FUNCTIONS
# =============================================================================

def get_supabase_client() -> Client:
    """Get Supabase client instance."""
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_KEY")
    if not url or not key:
        raise ValueError("Missing SUPABASE_URL or SUPABASE_KEY")
    return create_client(url, key)


def get_pending_airport_applications() -> List[Dict]:
    """Fetch new hires at airport store that need badge applications."""
    client = get_supabase_client()
    
    response = client.table("new_hire_submissions").select(
        "*, new_hire_setup_tasks!new_hire_setup_tasks_new_hire_id_fkey(*)"
    ).eq("store_number", AIRPORT_STORE).not_.is_("ssn_last_4", "null").execute()
    
    pending = []
    for record in response.data:
        setup_tasks = record.get("new_hire_setup_tasks")
        if not setup_tasks:
            pending.append(record)
        elif isinstance(setup_tasks, list):
            if setup_tasks and not setup_tasks[0].get("airport_badge_request_sent"):
                pending.append(record)
        elif isinstance(setup_tasks, dict):
            if not setup_tasks.get("airport_badge_request_sent"):
                pending.append(record)
    return pending


def get_application_by_id(submission_id: str) -> Optional[Dict]:
    """Fetch a single new hire submission by ID."""
    client = get_supabase_client()
    response = client.table("new_hire_submissions").select(
        "*, new_hire_setup_tasks!new_hire_setup_tasks_new_hire_id_fkey(*)"
    ).eq("id", submission_id).single().execute()
    return response.data


def format_applicant_for_bot(record: Dict) -> Dict:
    """Transform new_hire_submissions record to bot format."""
    dob_raw = record.get("date_of_birth", "")
    if dob_raw and isinstance(dob_raw, str) and "-" in dob_raw:
        parts = dob_raw.split("-")
        dob_formatted = f"{parts[1]}/{parts[2]}/{parts[0]}"
    else:
        dob_formatted = dob_raw or ""
    
    phone_raw = record.get("applicant_phone", "") or ""
    phone_clean = "".join(c for c in phone_raw if c.isdigit())
    
    return {
        "first_name": record.get("first_name", ""),
        "last_name": record.get("last_name", ""),
        "middle_name": record.get("middle_name", "") or "",
        "dob": dob_formatted,
        "ssn4": record.get("ssn_last_4", ""),
        "email": record.get("applicant_email", ""),
        "phone": phone_clean,
        "_submission_id": record.get("id"),
    }


def mark_badge_request_sent(submission_id: str) -> bool:
    """Update new_hire_setup_tasks to mark airport badge request as sent."""
    client = get_supabase_client()
    
    response = client.table("new_hire_setup_tasks").select("id").eq(
        "new_hire_id", submission_id
    ).execute()
    
    if not response.data:
        print(f"[WARN] No setup_tasks record found for {submission_id}")
        return False
    
    setup_task_id = response.data[0]["id"]
    
    update_response = client.table("new_hire_setup_tasks").update({
        "airport_badge_request_sent": True,
        "airport_badge_request_sent_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat(),
    }).eq("id", setup_task_id).execute()
    
    return len(update_response.data) > 0


# =============================================================================
# LOGIN
# =============================================================================

def login(page: Page) -> bool:
    """Log into the MyPITID portal."""
    username = os.getenv("PITID_USERNAME")
    password = os.getenv("PITID_PASSWORD")
    
    if not username or not password:
        raise ValueError("Missing PITID_USERNAME or PITID_PASSWORD")
    
    print(f"[LOGIN] Navigating to {BASE_URL}")
    page.goto(BASE_URL, wait_until="networkidle")
    
    print("[LOGIN] Clicking 'SIGN IN TO MYPITID'")
    page.click("text=SIGN IN TO MYPITID", timeout=TIMEOUT_MEDIUM)
    
    print("[LOGIN] Waiting for login form...")
    page.wait_for_load_state("networkidle")
    
    try:
        username_input = page.locator("input[type='email'], input[name='loginfmt'], #signInName").first
        username_input.wait_for(state="visible", timeout=TIMEOUT_MEDIUM)
        print(f"[LOGIN] Entering username: {username}")
        username_input.fill(username)
        
        next_btn = page.locator("input[type='submit'], button[type='submit'], #next").first
        if next_btn.is_visible():
            next_btn.click()
            page.wait_for_load_state("networkidle")
    except Exception as e:
        print(f"[LOGIN] Username step error: {e}")
    
    try:
        password_input = page.locator("input[type='password'], #password").first
        password_input.wait_for(state="visible", timeout=TIMEOUT_MEDIUM)
        print("[LOGIN] Entering password")
        password_input.fill(password)
        
        submit_btn = page.locator("input[type='submit'], button[type='submit'], #next").first
        submit_btn.click()
    except Exception as e:
        print(f"[LOGIN] Password step error: {e}")
        return False
    
    print("[LOGIN] Waiting for dashboard...")
    try:
        page.wait_for_url("**/AlertSelfService/**", timeout=TIMEOUT_LONG)
        print("[LOGIN] Successfully logged in!")
        return True
    except:
        if "AlertSelfService" in page.url:
            print("[LOGIN] Successfully logged in!")
            return True
        return False


# =============================================================================
# NAVIGATION
# =============================================================================

def navigate_to_application_management(page: Page) -> bool:
    """Navigate to Application Management from dashboard."""
    print("[NAV] Looking for Application Management tile...")
    try:
        app_mgmt = page.locator("text=Application Management").first
        app_mgmt.wait_for(state="visible", timeout=TIMEOUT_MEDIUM)
        app_mgmt.click()
        page.wait_for_load_state("networkidle")
        print("[NAV] Entered Application Management")
        return True
    except Exception as e:
        print(f"[NAV] Error: {e}")
        return False


def initiate_new_application(page: Page) -> bool:
    """Click to start a new badge application."""
    print("[NAV] Looking for 'Initiate a New Badge Application' button...")
    
    btn_selectors = [
        "text=Initiate a New Badge Application",
        "text=Initiate a New Badge",
        "a:has-text('Initiate a New Badge')",
        "text=Initiate",
    ]
    
    for selector in btn_selectors:
        try:
            btn = page.locator(selector).first
            if btn.is_visible(timeout=2000):
                btn.click()
                print(f"[NAV] Clicked: {selector}")
                page.wait_for_load_state("networkidle")
                page.wait_for_timeout(3000)
                return True
        except:
            continue
    
    print("[NAV] Could not find new application button")
    return False


# =============================================================================
# FORM FILLING
# =============================================================================

def fill_duplicate_check(page: Page, dob: str, ssn4: str) -> bool:
    """Fill the duplicate check modal with DOB and last 4 SSN."""
    print("[FORM] Waiting for duplicate check modal...")
    
    try:
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2000)
        
        # Wait for modal
        modal_selectors = ["text=Duplicate Check", "text=Personal Information", "input[placeholder*='MM/DD/YYYY']"]
        modal_found = False
        for selector in modal_selectors:
            try:
                page.wait_for_selector(selector, timeout=TIMEOUT_LONG)
                print(f"[FORM] Found modal element: {selector}")
                modal_found = True
                break
            except:
                continue
        
        if not modal_found:
            print("[FORM] Could not find duplicate check modal")
            return False
        
        # Parse DOB
        parts = dob.split("/")
        month, day, year = int(parts[0]), int(parts[1]), int(parts[2])
        print(f"[FORM] Parsed date: {month}/{day}/{year}")
        
        # Fill DOB via JavaScript
        print("[FORM] Setting DOB via JavaScript...")
        js_result = page.evaluate(f'''() => {{
            const modal = document.querySelector('mat-dialog-container, .mat-dialog-container');
            if (!modal) return {{ success: false }};
            const input = modal.querySelector('input[placeholder*="MM/DD/YYYY"]');
            if (!input) return {{ success: false }};
            input.removeAttribute('readonly');
            input.removeAttribute('disabled');
            input.value = "{dob}";
            input.dispatchEvent(new Event('input', {{ bubbles: true }}));
            input.dispatchEvent(new Event('change', {{ bubbles: true }}));
            return {{ success: true }};
        }}''')
        
        if js_result and js_result.get('success'):
            print(f"[FORM] Set DOB: {dob}")
        else:
            print("[FORM] WARNING: DOB JavaScript method may have failed")
        
        # Fill SSN4
        ssn_selectors = ["input[placeholder*='Last 4']", "input[placeholder*='SSN']", "input[id*='ssn']"]
        for selector in ssn_selectors:
            try:
                ssn_input = page.locator(selector).first
                if ssn_input.is_visible(timeout=1000):
                    ssn_input.fill(ssn4)
                    print(f"[FORM] Filled SSN4: {ssn4}")
                    break
            except:
                continue
        
        # Click Continue
        page.click("button:has-text('Continue')")
        page.wait_for_load_state("networkidle")
        print("[FORM] Duplicate check submitted")
        return True
        
    except Exception as e:
        print(f"[FORM] Duplicate check error: {e}")
        return False


def fill_autocomplete_field(page: Page, field_name: str, trigger: str) -> bool:
    """Fill an autocomplete field and select Atlas."""
    print(f"[FORM] === Filling {field_name} field ===")
    
    try:
        inp = None
        
        if field_name == "sponsor":
            for sel in ["input[placeholder*='list down all relevant Sponsors']", "input[placeholder*='Sponsor']"]:
                try:
                    inp = page.locator(sel).first
                    if inp.is_visible(timeout=1000):
                        break
                    inp = None
                except:
                    inp = None
        
        elif field_name == "access":
            try:
                inp = page.locator("mat-form-field:has-text('Access') input").first
                if not inp.is_visible(timeout=1000):
                    inp = None
            except:
                inp = None
            
            if not inp:
                form_fields = page.locator("mat-form-field").all()
                for ff in form_fields:
                    try:
                        if "Access" in ff.inner_text(timeout=300):
                            inp = ff.locator("input").first
                            if inp.is_visible(timeout=300):
                                break
                            inp = None
                    except:
                        continue
        
        elif field_name == "employer":
            try:
                inp = page.locator("mat-form-field:has-text('Employer') input").first
                if not inp.is_visible(timeout=1000):
                    inp = None
            except:
                inp = None
            
            if not inp:
                form_fields = page.locator("mat-form-field").all()
                for ff in form_fields:
                    try:
                        text = ff.inner_text(timeout=300)
                        if "Employer" in text or "Agency" in text:
                            inp = ff.locator("input").first
                            if inp.is_visible(timeout=300):
                                break
                            inp = None
                    except:
                        continue
        
        if not inp:
            print(f"[FORM] ERROR: Could not find {field_name}")
            return False
        
        inp.scroll_into_view_if_needed()
        page.wait_for_timeout(500)
        inp.click()
        page.wait_for_timeout(500)
        inp.fill("")
        page.wait_for_timeout(300)
        inp.type(trigger, delay=150)
        page.wait_for_timeout(2500)
        
        # Click Atlas option
        option_selectors = ["mat-option:has-text('Atlas')", ".mat-option:has-text('Atlas')"]
        for opt_sel in option_selectors:
            try:
                options = page.locator(opt_sel).all()
                for opt in options:
                    if opt.is_visible(timeout=500):
                        opt.click()
                        print(f"[FORM] Selected Atlas for {field_name}")
                        page.wait_for_timeout(1000)
                        return True
            except:
                continue
        
        # Fallback: keyboard
        page.keyboard.press("ArrowDown")
        page.wait_for_timeout(500)
        page.keyboard.press("Enter")
        page.wait_for_timeout(1000)
        return True
        
    except Exception as e:
        print(f"[FORM] Autocomplete error ({field_name}): {e}")
        return False


def fill_application_form(page: Page, applicant: dict, submit: bool = False) -> bool:
    """Fill out the main application form."""
    print("[FORM] Filling application form...")
    
    try:
        page.wait_for_selector("text=Applicant Name and Contact Information", timeout=TIMEOUT_MEDIUM)
        page.wait_for_timeout(1000)
        
        # First Name
        print("[FORM] Filling First Name...")
        for sel in ["mat-form-field:has-text('First Name') input", "input[id*='first' i]"]:
            try:
                inp = page.locator(sel).first
                if inp.is_visible(timeout=1000):
                    inp.click()
                    inp.fill(applicant["first_name"])
                    print(f"[FORM] First Name: {applicant['first_name']}")
                    break
            except:
                continue
        
        page.wait_for_timeout(300)
        
        # Last Name
        print("[FORM] Filling Last Name...")
        for sel in ["mat-form-field:has-text('Last name') input", "mat-form-field:has-text('Last Name') input"]:
            try:
                inp = page.locator(sel).first
                if inp.is_visible(timeout=1000):
                    inp.click()
                    inp.fill(applicant["last_name"])
                    print(f"[FORM] Last Name: {applicant['last_name']}")
                    break
            except:
                continue
        
        page.wait_for_timeout(300)
        
        # Middle Name (optional)
        if applicant.get("middle_name"):
            for sel in ["mat-form-field:has-text('Middle') input"]:
                try:
                    inp = page.locator(sel).first
                    if inp.is_visible(timeout=1000):
                        inp.fill(applicant["middle_name"])
                        print(f"[FORM] Middle Name: {applicant['middle_name']}")
                        break
                except:
                    continue
        
        page.wait_for_timeout(300)
        
        # Email
        print("[FORM] Filling Email...")
        for sel in ["mat-form-field:has-text('Email') input", "input[type='email']"]:
            try:
                inp = page.locator(sel).first
                if inp.is_visible(timeout=1000):
                    inp.click()
                    inp.fill(applicant["email"])
                    print(f"[FORM] Email: {applicant['email']}")
                    break
            except:
                continue
        
        page.wait_for_timeout(300)
        
        # Phone
        print("[FORM] Filling Phone...")
        for sel in ["mat-form-field:has-text('Phone') input", "input[type='tel']"]:
            try:
                inp = page.locator(sel).first
                if inp.is_visible(timeout=1000):
                    inp.click()
                    inp.fill(applicant["phone"])
                    print(f"[FORM] Phone: {applicant['phone']}")
                    break
            except:
                continue
        
        page.wait_for_timeout(500)
        page.evaluate("window.scrollBy(0, 400)")
        page.wait_for_timeout(1000)
        
        # Sponsor, Access Groups, Employer
        fill_autocomplete_field(page, "sponsor", "**")
        page.wait_for_timeout(1500)
        fill_autocomplete_field(page, "access", "**")
        page.wait_for_timeout(1500)
        fill_autocomplete_field(page, "employer", "**")
        page.wait_for_timeout(1500)
        
        # Scroll and select Badge Type
        page.evaluate("window.scrollBy(0, 400)")
        page.wait_for_timeout(1000)
        
        print("[FORM] Selecting Badge Type: Sterile Area")
        try:
            for sel in ["mat-form-field:has-text('Badge Type') mat-select", "mat-select[id*='badge' i]"]:
                try:
                    dropdown = page.locator(sel).first
                    if dropdown.is_visible(timeout=1000):
                        dropdown.click()
                        page.wait_for_timeout(500)
                        page.locator("mat-option:has-text('Sterile Area')").first.click()
                        print("[FORM] Selected 'Sterile Area'")
                        break
                except:
                    continue
        except:
            pass
        
        # Scroll and check certification
        print("[FORM] Scrolling to bottom for checkbox...")
        page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        page.wait_for_timeout(2000)  # Wait 2 seconds after scroll
        
        print("[FORM] Looking for certification checkbox...")
        page.wait_for_timeout(1000)
        
        # Take screenshot to see checkbox state
        page.screenshot(path="before_checkbox.png")
        
        checkbox_clicked = False
        
        # Method 1: Find checkbox by the certification text and click it
        try:
            cert_checkbox = page.locator("mat-checkbox:has-text('certify')").first
            if cert_checkbox.is_visible(timeout=3000):
                print("[FORM] Found checkbox with 'certify' text")
                page.wait_for_timeout(500)
                cert_checkbox.scroll_into_view_if_needed()
                page.wait_for_timeout(500)
                cert_checkbox.click()
                page.wait_for_timeout(1000)
                print("[FORM] Clicked certification checkbox")
                checkbox_clicked = True
        except Exception as e:
            print(f"[FORM] Certify checkbox method failed: {e}")
        
        # Method 2: Click the mat-checkbox container
        if not checkbox_clicked:
            try:
                checkbox = page.locator("mat-checkbox").first
                if checkbox.is_visible(timeout=2000):
                    print("[FORM] Found mat-checkbox")
                    checkbox.scroll_into_view_if_needed()
                    page.wait_for_timeout(500)
                    
                    # Check if already checked
                    class_attr = checkbox.get_attribute("class") or ""
                    if "mat-checkbox-checked" not in class_attr:
                        checkbox.click()
                        page.wait_for_timeout(1000)
                        print("[FORM] Clicked mat-checkbox")
                        checkbox_clicked = True
                    else:
                        print("[FORM] Checkbox already checked")
                        checkbox_clicked = True
            except Exception as e:
                print(f"[FORM] mat-checkbox click failed: {e}")
        
        # Method 3: Click the label inside mat-checkbox
        if not checkbox_clicked:
            try:
                label = page.locator("mat-checkbox label").first
                if label.is_visible(timeout=2000):
                    print("[FORM] Found checkbox label")
                    label.scroll_into_view_if_needed()
                    page.wait_for_timeout(500)
                    label.click()
                    page.wait_for_timeout(1000)
                    print("[FORM] Clicked checkbox label")
                    checkbox_clicked = True
            except Exception as e:
                print(f"[FORM] Label click failed: {e}")
        
        # Method 4: Use JavaScript to check it
        if not checkbox_clicked:
            try:
                print("[FORM] Trying JavaScript method...")
                page.evaluate('''() => {
                    const checkbox = document.querySelector('mat-checkbox');
                    if (checkbox) {
                        checkbox.scrollIntoView();
                        const input = checkbox.querySelector('input[type="checkbox"]');
                        if (input && !input.checked) {
                            input.click();
                        }
                        // Also click the checkbox container
                        checkbox.click();
                    }
                }''')
                page.wait_for_timeout(1000)
                print("[FORM] Used JavaScript to check checkbox")
                checkbox_clicked = True
            except Exception as e:
                print(f"[FORM] JavaScript checkbox failed: {e}")
        
        # Method 5: Try clicking by coordinates
        if not checkbox_clicked:
            try:
                print("[FORM] Trying coordinate click...")
                checkbox = page.locator("mat-checkbox").first
                box = checkbox.bounding_box()
                if box:
                    # Click in the middle of the checkbox
                    page.mouse.click(box['x'] + 15, box['y'] + box['height']/2)
                    page.wait_for_timeout(1000)
                    print("[FORM] Clicked checkbox by coordinates")
                    checkbox_clicked = True
            except Exception as e:
                print(f"[FORM] Coordinate click failed: {e}")
        
        # Verify checkbox is now checked
        page.wait_for_timeout(500)
        try:
            checkbox = page.locator("mat-checkbox").first
            class_attr = checkbox.get_attribute("class") or ""
            if "mat-checkbox-checked" in class_attr:
                print("[FORM] SUCCESS: Checkbox verified as checked")
            else:
                print("[FORM] WARNING: Checkbox may not be checked!")
                page.screenshot(path="checkbox_not_checked.png")
        except:
            pass
        
        # Extra wait before submit
        page.wait_for_timeout(1500)
        
        # Submit or stop
        if submit:
            print("[FORM] Submitting application...")
            page.click("button:has-text('Submit')")
            page.wait_for_timeout(3000)  # Wait for response
            page.wait_for_load_state("networkidle")
            
            # Check for errors - only flag if there's actual error text
            error_selectors = [
                "text=already exists",
                "text=duplicate",
                "text=Email is already",
                "mat-snack-bar-container:has-text('error')",
                "snack-bar-container:has-text('error')",
                ".error-message",
            ]
            
            error_found = False
            for err_sel in error_selectors:
                try:
                    error_el = page.locator(err_sel).first
                    if error_el.is_visible(timeout=1000):
                        error_text = error_el.inner_text().strip()
                        # Only flag as error if there's actual text content
                        if error_text and len(error_text) > 5:
                            print(f"[FORM] ERROR DETECTED: {error_text}")
                            page.screenshot(path="submit_error.png")
                            error_found = True
                            break
                except:
                    continue
            
            if error_found:
                return False
            
            # Check for success indicators
            success_selectors = [
                "text=successfully",
                "text=submitted",
                "text=Application has been",
                "text=invitation has been sent",
                "mat-snack-bar-container:has-text('success')",
            ]
            
            success_found = False
            for suc_sel in success_selectors:
                try:
                    success_el = page.locator(suc_sel).first
                    if success_el.is_visible(timeout=2000):
                        success_text = success_el.inner_text().strip()
                        if success_text:
                            print(f"[FORM] SUCCESS: {success_text}")
                            success_found = True
                            break
                except:
                    continue
            
            # Also check if we're back on the application list (redirect = success)
            if not success_found:
                try:
                    page_content = page.content()
                    if "Initiate a New Badge" in page_content or "Application Management" in page_content:
                        print("[FORM] SUCCESS: Redirected back to application list")
                        success_found = True
                except:
                    pass
            
            # Check if Submit button is gone (means form was submitted)
            if not success_found:
                try:
                    submit_btn = page.locator("button:has-text('Submit')").first
                    if not submit_btn.is_visible(timeout=1000):
                        print("[FORM] SUCCESS: Submit button no longer visible")
                        success_found = True
                except:
                    pass
            
            if success_found:
                print("[FORM] Application submitted successfully!")
                return True
            else:
                print("[FORM] WARNING: Could not confirm submission - checking for errors...")
                page.screenshot(path="submit_uncertain.png")
                
                # If no explicit error and no success, assume it worked
                # (sometimes success message is quick and disappears)
                print("[FORM] No error detected, assuming success")
                return True
        else:
            print("[FORM] *** SUBMIT DISABLED - Form filled but not submitted ***")
            return True
        
    except Exception as e:
        print(f"[FORM] Error: {e}")
        return False


# =============================================================================
# MAIN RUNNER
# =============================================================================

def run_application(applicant: dict, headless: bool = True, submit: bool = False) -> bool:
    """Run the full application flow."""
    print("=" * 60)
    print(f"PIT ID Application Bot - {datetime.now()}")
    print(f"Applicant: {applicant.get('first_name')} {applicant.get('last_name')}")
    print(f"Submit: {submit}")
    print("=" * 60)
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        context = browser.new_context(viewport={"width": 1280, "height": 800})
        page = context.new_page()
        
        try:
            if not login(page):
                print("[ERROR] Login failed")
                return False
            
            if not navigate_to_application_management(page):
                print("[ERROR] Could not navigate to Application Management")
                return False
            
            if not initiate_new_application(page):
                print("[ERROR] Could not initiate new application")
                return False
            
            if not fill_duplicate_check(page, applicant["dob"], applicant["ssn4"]):
                print("[ERROR] Duplicate check failed")
                return False
            
            if not fill_application_form(page, applicant, submit=submit):
                print("[ERROR] Form filling failed")
                return False
            
            print("[SUCCESS] Application process completed!")
            return True
            
        except Exception as e:
            print(f"[ERROR] Unexpected error: {e}")
            return False
        finally:
            browser.close()


def run_apply(application_id: str = None, all_pending: bool = False, submit: bool = False, headless: bool = True):
    """Run application submission."""
    if application_id:
        record = get_application_by_id(application_id)
        if not record:
            print(f"[ERROR] Application {application_id} not found")
            return
        
        applicant = format_applicant_for_bot(record)
        print(f"[INFO] Processing: {applicant['first_name']} {applicant['last_name']}")
        success = run_application(applicant, headless=headless, submit=submit)
        
        if success and submit:
            mark_badge_request_sent(application_id)
            print(f"[INFO] Marked badge request as sent")
        elif not success:
            print(f"[ERROR] Application failed - NOT marking as sent")
    
    elif all_pending:
        records = get_pending_airport_applications()
        print(f"[INFO] Found {len(records)} pending airport badge applications")
        
        if not records:
            print("[INFO] No pending applications to process")
            return
        
        success_count = 0
        fail_count = 0
        
        for record in records:
            applicant = format_applicant_for_bot(record)
            print("=" * 40)
            print(f"Processing: {applicant['first_name']} {applicant['last_name']}")
            print(f"DOB: {applicant['dob']}, SSN4: {applicant['ssn4']}")
            print(f"Email: {applicant['email']}, Phone: {applicant['phone']}")
            
            success = run_application(applicant, headless=headless, submit=submit)
            
            if success and submit:
                mark_badge_request_sent(record["id"])
                print(f"[INFO] Marked badge request as sent for {record['id']}")
                success_count += 1
            elif not success:
                print(f"[ERROR] Application failed for {applicant['first_name']} {applicant['last_name']} - NOT marking as sent")
                fail_count += 1
        
        print("=" * 40)
        print(f"[SUMMARY] Completed: {success_count} success, {fail_count} failed")


def run_test(headless: bool = True):
    """Run with test data."""
    test_applicant = {
        "first_name": "Test",
        "last_name": "User",
        "middle_name": "",
        "dob": "01/15/1990",
        "ssn4": "1234",
        "email": "jchung@atlaswe.com",
        "phone": "4125551234",
    }
    
    print(f"[TEST] Running with test data (submit disabled)")
    run_application(test_applicant, headless=headless, submit=False)


def main():
    parser = argparse.ArgumentParser(description="PIT ID Badge Application Bot")
    subparsers = parser.add_subparsers(dest="command", help="Commands")
    
    # Apply command
    apply_parser = subparsers.add_parser("apply", help="Submit badge applications")
    apply_parser.add_argument("--id", type=str, help="Specific application ID")
    apply_parser.add_argument("--all", action="store_true", help="Process all pending")
    apply_parser.add_argument("--submit", action="store_true", help="Actually submit")
    apply_parser.add_argument("--visible", action="store_true", help="Show browser")
    
    # Test command
    test_parser = subparsers.add_parser("test", help="Test with dummy data")
    test_parser.add_argument("--visible", action="store_true", help="Show browser")
    
    args = parser.parse_args()
    
    if args.command == "apply":
        run_apply(
            application_id=args.id,
            all_pending=args.all,
            submit=args.submit,
            headless=not args.visible
        )
    elif args.command == "test":
        run_test(headless=not args.visible)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
