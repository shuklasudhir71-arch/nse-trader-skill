# NSE Trader skill (भारतीय शेयर बाज़ार के लिए)

NSE/BSE पर इंट्राडे ट्रेडिंग के लिए Claude की 11 स्किल्स। यह [day-trader-skill](https://github.com/Jakeschincariol/day-trader-skill)
(MIT लाइसेंस) के तरीके पर आधारित है, लेकिन भारतीय बाज़ार के लिए नए सिरे से लिखा गया है:

- **भारतीय समय (IST)**: बाज़ार सुबह 9:15 से दोपहर 3:30 तक, प्री-ओपन 9:00 से 9:15
- **NSE की छुट्टियाँ**: `src/holidays.json` में (हर साल nseindia.com से मिलान करें)
- **₹ में हिसाब**: भारतीय तरीके के कॉमा के साथ, जैसे ₹1,23,456
- **चार्ज**: Zerodha जैसे शुल्क (ब्रोकरेज, STT, एक्सचेंज शुल्क, SEBI शुल्क, स्टाम्प ड्यूटी, GST)
- **ऑर्डर टिकट**: Zerodha Kite के फ़ॉर्मेट में (MIS/CNC, LIMIT और SL-M)
- **डेटा**: Yahoo Finance (RELIANCE.NS, TCS.BO), Nifty Indices/NSE की स्टॉक लिस्ट और Google News

कोई API key नहीं चाहिए, कुछ pip install नहीं करना। बस Python 3.8 या उससे नया वर्ज़न चाहिए।

## इंस्टॉल

```bash
cp -r skills/nse-* ~/.claude/skills/
```

## स्किल्स

| कमांड | काम |
| --- | --- |
| `/nse-scanner` | Nifty 50/500 या सभी NSE स्टॉक्स में ब्रेकआउट, बुल फ़्लैग, ओवरसोल्ड और ओपनिंग-रेंज ब्रेकआउट ढूँढता है |
| `/nse-news` | हर स्टॉक की ताज़ा ख़बरें, टैग के साथ (नतीजे, ऑर्डर, डिविडेंड, SEBI...) |
| `/nse-entry` | 5 मिनट की कैंडल देखकर बताता है कि कब घुसना है |
| `/nse-exit` | स्टॉप-लॉस, टारगेट और स्क्वेयर-ऑफ़ का समय तय करता है |
| `/nse-timemachine` | ट्रेड को 10,000 बार सिमुलेट करके संभावनाएँ बताता है, और पिछले दिनों पर वही नियम दोबारा चलाता है |
| `/nse-budget` | कितने शेयर लेने हैं, और चार्ज कितने लगेंगे |
| `/nse-risk` | हर ऑर्डर को आपकी सीमाओं से जाँचता है और PASS या BLOCK बताता है |
| `/nse-broker` | Kite ऑर्डर टिकट बनाता है। आपकी "हाँ" के बिना कुछ नहीं होता |
| `/nse-alerts` | कीमत, स्टॉप, टारगेट और समय के अलर्ट देता है |
| `/nse-deals` | NSE के Bulk और Block Deals: बड़े निवेशकों (म्यूचुअल फ़ंड, विदेशी फ़ंड, प्रमोटर) की ख़रीद-बिक्री, HFT को छाँटकर |
| `/nse-analyst` | तिमाही नतीजे: बिक्री, मार्जिन, मुनाफ़ा, EPS, भारतीय वित्त-वर्ष के हिसाब से (Q2 FY27) |

## एक ट्रेड, शुरू से अंत तक

```bash
python3 skills/nse-budget/nse.py init                        # nse/config.json: अपनी पूँजी और रिस्क भरें
python3 skills/nse-scanner/nse.py universe --index nifty500
python3 skills/nse-scanner/nse.py scan orb --at 09:45
python3 skills/nse-entry/nse.py entry check RELIANCE
python3 skills/nse-exit/nse.py plan --stop or-mid --target 2R
python3 skills/nse-timemachine/nse.py sim
python3 skills/nse-budget/nse.py size
python3 skills/nse-risk/nse.py risk
python3 skills/nse-broker/nse.py ticket show
python3 skills/nse-alerts/nse.py alerts add --from-plan
python3 skills/nse-deals/nse.py deals                       # कल के बड़े सौदे
python3 skills/nse-analyst/nse.py results RELIANCE,TCS      # तिमाही नतीजे
python3 skills/nse-budget/nse.py review --days 30           # पेपर ट्रेडिंग का 30 दिन का हिसाब
```

सारी फ़ाइलें उसी फ़ोल्डर के अंदर `nse/` में बनती हैं जहाँ से आप कमांड चलाते हैं।

## टेस्ट

```bash
python3 -m unittest discover -s tests -v
```

टेस्ट नकली (synthetic) डेटा पर बिना इंटरनेट के चलते हैं।

## ज़रूरी बातें

- **यह निवेश सलाह नहीं है।** यह SEBI-पंजीकृत सलाहकार नहीं है। SEBI के अध्ययन के अनुसार इंट्राडे ट्रेड करने वाले
  ज़्यादातर लोग घाटे में रहते हैं।
- **पहले पेपर ट्रेडिंग करें।** डिफ़ॉल्ट रूप से `"paper": true` है। Zerodha में अलग पेपर अकाउंट नहीं होता,
  इसलिए पेपर मोड में कोई ऑर्डर नहीं जाता, सिर्फ़ टिकट बनता है।
- **कोई ऑर्डर अपने-आप नहीं जाता।** तीन शर्तें पूरी होनी ज़रूरी हैं: risk में PASS, चैट में आपकी साफ़ "हाँ",
  और Claude का "Allow once" कार्ड।
- **डेटा मुफ़्त है और देर से आता है।** Yahoo का डेटा 1 से 15 मिनट पीछे हो सकता है।
  ऑर्डर उसी भाव पर भरता है जो ब्रोकर पर है।
- **स्टॉप-लॉस गारंटी नहीं है।** गैप या सर्किट लगने पर SL-M ऑर्डर ट्रिगर से काफ़ी नीचे भर सकता है।
- **चार्ज और टिक साइज़ बदलते रहते हैं।** अपने ब्रोकर का मौजूदा रेट कार्ड और NSE का टिक साइज़ जाँच लें।
- **टैक्स:** इंट्राडे का मुनाफ़ा speculative business income माना जाता है। यह टैक्स सलाह नहीं है।

MIT लाइसेंस।
