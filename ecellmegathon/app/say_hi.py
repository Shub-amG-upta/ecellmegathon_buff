"""
Hindi spoken replies (2026-10-11): the assistant's sentences in Hindi, for a Hindi TTS voice.

Only the SPOKEN reply changes: the chat text, the form and the saved report stay English. Every sentence the app
says comes from a template in app/dialog.py or a field's `ask` in the schema; each template is matched here and
said in Hindi, with the user's own values (places, names, actions) kept exactly as they were said. A sentence
that matches no template is passed through unchanged (it is still spoken, in English words).

Words a Hindi voice would mispronounce in Latin letters (severity levels, AM/PM, "submit", report ids) are
written in Devanagari.

Stdlib only.
"""
from __future__ import annotations

import re

_ASK = {
    "What happened? Tell me in your own words.": "क्या हुआ? अपने शब्दों में बताइए।",
    "What happened?": "क्या हुआ?",
    "Tell me in your own words.": "अपने शब्दों में बताइए।",
    "What kind of incident was it?": "किस तरह की घटना हुई?",
    "How serious is it: low, medium, high or critical?": "यह कितना गंभीर है: लो, मीडियम, हाई या क्रिटिकल?",
    "Where did it happen?": "यह कहाँ हुआ?",
    "When did it happen?": "यह कब हुआ?",
    "Was anyone involved or hurt?": "क्या कोई शामिल था, या किसी को चोट लगी?",
    "Was any equipment or asset involved?": "क्या कोई उपकरण या मशीन शामिल थी?",
    "Has anything been done about it yet?": "क्या अब तक इसके लिए कुछ किया गया है?",
    "Did anyone else see it?": "क्या किसी और ने इसे देखा?",
    # the 15-field form's extra questions (FORM=15)
    "What were they doing just before it happened?": "घटना से ठीक पहले वे क्या कर रहे थे?",
    "Do you know what caused it?": "क्या आपको पता है कि यह किस वजह से हुआ?",
    "Was any property or equipment damaged?": "क्या कोई सामान या उपकरण खराब हुआ?",
    "Is the hazard under control now?": "क्या खतरा अब काबू में है?",
    "Who has been informed?": "किसे सूचना दी गई है?",
    "Could you describe what happened?": "क्या आप बता सकते हैं कि क्या हुआ?",
    "Say submit, or tell me what to change.": "सबमिट बोलिए, या बताइए कि क्या बदलना है।",
    "Sorry, I didn't catch that.": "माफ़ कीजिए, यह सुनाई नहीं दिया।",
    "Sorry, I had trouble with that.": "माफ़ कीजिए, कुछ दिक्कत हुई।",
    "Could you say it again?": "क्या आप फिर से बोल सकते हैं?",
    "Sorry, I didn't get anything for the report from that.": "माफ़ कीजिए, इससे रिपोर्ट के लिए कुछ नहीं मिला।",
    "Okay, I've discarded this report.": "ठीक है, यह रिपोर्ट हटा दी गई है।",
    "Updated.": "बदल दिया गया।",
    "Here's what I have.": "मेरे पास यह जानकारी है।",
    "Say a new severity to change it.": "बदलने के लिए नई गंभीरता बोलिए।",
}

TYPE = {
    "Fire": "आग", "Electrical incident": "बिजली की घटना", "Water leak": "पानी का रिसाव",
    "Gas leak": "गैस का रिसाव", "Chemical incident": "रासायनिक घटना", "Slip or trip": "फिसलना या ठोकर लगना",
    "Fall from height": "ऊँचाई से गिरना", "Struck by an object": "किसी चीज़ से चोट लगना",
    "Caught in machinery": "मशीन में फँसना", "Equipment failure": "उपकरण की खराबी", "Lift incident": "लिफ्ट की घटना",
    "Structural collapse": "ढाँचा गिरना", "Medical emergency": "मेडिकल इमरजेंसी",
    "Security incident": "सुरक्षा की घटना", "Other incident": "अन्य घटना",
}
SEVERITY = {"low": "लो", "medium": "मीडियम", "high": "हाई", "critical": "क्रिटिकल"}
_FIELD = {"type of incident": "घटना का प्रकार", "severity": "गंभीरता", "location": "जगह",
          "time it happened": "घटना का समय", "description": "विवरण"}
_LABEL = {"Where": "जगह", "When": "समय", "People involved": "शामिल लोग", "Equipment": "उपकरण",
          "Actions taken": "की गई कार्रवाई", "Witnesses": "गवाह", "Task": "काम", "Cause": "कारण",
          "Damage": "नुकसान", "Hazard": "खतरा", "Informed": "सूचित किया"}

_TYPES = "|".join(sorted(map(re.escape, TYPE), key=len, reverse=True))
_SEVS = "|".join(SEVERITY)
_TYPE_SEV = re.compile(rf"^({_TYPES}), ({_SEVS}) severity\.$")          # the read-back's 2nd sentence
_FLOOR = re.compile(rf"^({_TYPES}) is usually at least ({_SEVS}) severity; you said ({_SEVS})\.$")
_NEED = re.compile(r"^I still need the (" + "|".join(map(re.escape, _FIELD)) + r")\.$")
_SUBMITTED = re.compile(r"^Report ([A-Z]+)-(\d+) submitted\.$")
_LABELLED = re.compile(r"^(" + "|".join(sorted(map(re.escape, _LABEL), key=len, reverse=True)) + r"): (.+)\.$")
# Sentence ends: ". " / "? " / "! " followed by a capital letter, so "10:00 AM" or "Room 2." inside values stay whole.
_SPLIT = re.compile(r"(?<=[.?!;])\s+(?=[A-Z])")


def _time_words(value: str) -> str:
    """'around 3:00 PM yesterday' -> 'लगभग 3:00 पी एम कल' (the part of a time a Hindi voice must say in Hindi)."""
    out = re.sub(r"\baround\b", "लगभग", value)
    out = re.sub(r"\btoday\b", "आज", out)
    out = re.sub(r"\byesterday\b", "कल", out)
    out = re.sub(r"\bAM\b", "ए एम", out)
    return re.sub(r"\bPM\b", "पी एम", out)


def _sentence(s: str) -> str:
    if s in _ASK:
        return _ASK[s]
    if (m := _TYPE_SEV.match(s)):
        return f"{TYPE[m[1]]}, गंभीरता {SEVERITY[m[2]]}।"
    if (m := _FLOOR.match(s)):
        return (f"{TYPE[m[1]]} आमतौर पर कम से कम {SEVERITY[m[2]]} गंभीरता की होती है; "
                f"आपने {SEVERITY[m[3]]} कहा।")
    if (m := _NEED.match(s)):
        return f"मुझे अभी {_FIELD[m[1]]} चाहिए।"
    if (m := _SUBMITTED.match(s)):
        letters = " ".join({"I": "आई", "R": "आर"}.get(c, c) for c in m[1])
        return f"रिपोर्ट {letters} {int(m[2])} सबमिट हो गई है।"
    if (m := _LABELLED.match(s)):
        value = _time_words(m[2]) if m[1] == "When" else m[2]
        return f"{_LABEL[m[1]]}: {value}।"
    return s                                   # no template: said as is


def to_hindi(say: str) -> str:
    """The assistant's reply, said in Hindi. User values inside it are kept as they were said."""
    if not say:
        return say
    return " ".join(_sentence(s.strip()) for s in _SPLIT.split(say.strip()) if s.strip())
