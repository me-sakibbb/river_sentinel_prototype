"""SIMULATED alerting. Nothing here sends a message anywhere.

This shows what a push alert to the BWDB division office or a union parishad
office could look like. There is no SMS gateway, telecom API or e-mail
integration in this prototype, and none should be added without a proper
agreement with the recipients.
"""
import config

SIMULATED_BANNER = "SIMULATED ALERT - NOT SENT - prototype demo only"

RECIPIENTS = [  # roles only; no real phone numbers or addresses
    {"role": "BWDB Sirajganj O&M Division (executive engineer's office)", "channel": "SMS (simulated)"},
    {"role": "Union Parishad office for the affected union", "channel": "SMS (simulated)"},
    {"role": "Upazila Nirbahi Officer (UNO) office", "channel": "App push (simulated)"},
]


def build_alert(site_name, run, stats, scenes):
    """Return a simulated alert dict, or None if the site is below threshold."""
    ha = stats["bank_erosion_ha"]
    if ha < config.ALERT_THRESHOLD_HA:
        return None
    flags = stats["review_flags"]
    msg = (f"[SIMULATED] Riverbank change screening: about {ha:.1f} ha of land "
           f"along the Jamuna in {site_name} appears to have turned to water "
           f"between satellite passes on {scenes['before_dates'][-1]} and "
           f"{scenes['after_dates'][-1]}. Please verify on the ground.")
    if flags:
        msg += (f" {flags} new-water patch(es) have an unusual pit-like shape "
                f"and are marked for human review (possible extraction; NOT confirmed).")
    return {
        "banner": SIMULATED_BANNER,
        "site": site_name,
        "run": run["id"],
        "severity": "high" if ha >= 4 * config.ALERT_THRESHOLD_HA else "watch",
        "message": msg,
        "recipients": RECIPIENTS,
        "sent": False,
    }


def print_alert(alert):
    line = "=" * len(alert["banner"])
    print(f"\n    {line}\n    {alert['banner']}\n    {line}")
    print("    To: " +"; ".join(r["role"] for r in alert["recipients"]))
    print(f"    {alert['message']}\n    (No message was sent.)\n")


# ---------------------------------------------------------------------------
# Forecast alerts (SIMULATED): what residents and officials could receive
# BEFORE the monsoon, from the erosion forecast (run_forecast.py).
# ---------------------------------------------------------------------------
FORECAST_RECIPIENTS = [
    {"role": "Mobile subscribers in the at-risk cells (location-based broadcast)",
     "channel": "Bangla SMS + voice call for people who can't read SMS (simulated)"},
    {"role": "Union Parishad chairman and ward members", "channel": "SMS (simulated)"},
    {"role": "BWDB Sirajganj O&M Division", "channel": "App push with map link (simulated)"},
    {"role": "Upazila Nirbahi Officer (UNO) and disaster management committee",
     "channel": "App push (simulated)"},
]


SITE_NAMES_BN = {"kazipur": "কাজীপুর উপজেলা", "chauhali": "চৌহালী উপজেলা"}


def build_forecast_alert(site_id, site_name, watch_items, issued_from):
    """A simulated pre-monsoon warning for one upazila's highest-risk bank cells."""
    if not watch_items:
        return None
    n = len(watch_items)
    bn = ("[পরীক্ষামূলক বার্তা] যমুনা নদীভাঙনের আগাম সতর্কতা: উপগ্রহ-চিত্র বিশ্লেষণে "
          f"আপনার এলাকার ({SITE_NAMES_BN.get(site_id, site_name)}) নদীতীরের কিছু অংশে আগামী বর্ষায় ভাঙনের ঝুঁকি বেশি। "
          "ঘরের জরুরি কাগজপত্র, গবাদিপশু ও মূল্যবান জিনিস সরানোর প্রস্তুতি নিন। "
          "ইউনিয়ন পরিষদের সাথে যোগাযোগ করুন। "
          "ভাঙন দেখলে উত্তর দিন: ১ = ভাঙন শুরু হয়েছে, ২ = এখনো ভাঙন নেই।")
    en = (f"[SIMULATED] Early erosion warning: satellite analysis shows a high risk "
          f"of riverbank erosion this monsoon along parts of the Jamuna bank in "
          f"{site_name}. Get ready to move important papers, livestock and "
          f"valuables. Contact your Union Parishad. If you see erosion, reply: "
          f"1 = erosion has started, 2 = no erosion yet.")
    official = (f"[SIMULATED] Erosion forecast ({issued_from}): {n} bank stretches of "
                f"240 m in {site_name} are on HIGH ALERT (tested precision is on the dashboard). The map "
                f"and reasons for each are in the dashboard. Suggested use: prioritise field "
                f"checks and emergency geobag stock; notify schools and markets in these cells.")
    return {
        "banner": SIMULATED_BANNER,
        "site": site_name,
        "message_bn": bn,
        "message_en": en,
        "message_official": official,
        "recipients": FORECAST_RECIPIENTS,
        "reply_loop": "Replies ('1' / '2') and photos from residents would be checked and "
                      "used as new, ground-truth labels to retrain the forecast every year.",
        "sent": False,
    }
