import streamlit as st
import google.genai as genai
from gtts import gTTS
from io import BytesIO
import re
import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse
from product_list import loadplist

from streamlit_webrtc import webrtc_streamer, WebRtcMode
from streamlit_webrtc import AudioProcessorBase

import av


client = genai.Client(api_key=st.secrets["MY_API_KEY"])

product_list = loadplist()

if 'chat_history' not in st.session_state:
    st.session_state.chat_history = []
if 'is_recording' not in st.session_state:
    st.session_state.is_recording = False
if 'recognition_result' not in st.session_state:
    st.session_state.recognition_result = None

def extract_url_from_text(text):
    url_pattern = r'https?://[^\s]+'
    urls = re.findall(url_pattern, text)
    return urls[0] if urls else None

def fetch_url_content(url):
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        
        soup = BeautifulSoup(response.content, 'html.parser')
        
        for script in soup(["script", "style", "nav", "footer", "header"]):
            script.decompose()
        
        text = soup.get_text()
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text = '\n'.join(chunk for chunk in chunks if chunk)
        
        metadata = {
            'title': soup.title.string if soup.title else '',
            'prices': [],
            'product_names': [],
            'descriptions': []
        }
        
        price_patterns = [r'\$\d+\.?\d*', r'₹\d+', r'€\d+\.?\d*', r'£\d+\.?\d*']
        for pattern in price_patterns:
            prices = re.findall(pattern, text)
            if prices:
                metadata['prices'].extend(prices[:10])
        
        lines = text.split('\n')
        for line in lines:
            line_lower = line.lower()
            if len(line) < 100 and len(line) > 5:
                if any(keyword in line_lower for keyword in ['shirt', 'dress', 'jeans', 'hoodie', 'blazer', 'skirt', 'polo', 't-shirt']):
                    metadata['product_names'].append(line.strip())
                elif re.search(r'\b(men|women|kids)\b', line_lower) or re.search(r'\b(size|color)\b', line_lower):
                    metadata['product_names'].append(line.strip())
        
        metadata['prices'] = list(set(metadata['prices']))[:5]
        metadata['product_names'] = list(set(metadata['product_names']))[:10]
        
        if len(text) > 4000:
            text = text[:4000] + "... [content truncated]"
            
        return text, metadata
        
    except Exception as e:
        return f"Error fetching URL: {str(e)}", {}

def compare_and_suggest(url_content, url_metadata):
    try:
        comparison_prompt = f"""

        OUR PRODUCTS:
        {product_list}

        COMPETITOR URL PRODUCTS:
        URL Title: {url_metadata.get('title', 'N/A')}
        Products Found: {', '.join(url_metadata.get('product_names', ['Not identified']))}
        Prices Found: {', '.join(url_metadata.get('prices', ['Not identified']))}

        COMPETITOR WEBSITE CONTENT (Summary):
        {url_content[:2000] if len(url_content) > 2000 else url_content}

        TASK: Do a DIRECT comparison and suggest OUR products:

        1. **COMPARISON TABLE:**
           For each competitor product you can identify, find our closest match and compare:
           - Product Type
           - Price (Theirs vs Ours)
           - Key Features
           - Quality/Rating

        2. **OUR SUGGESTIONS (Most Important):**
           List OUR products that are BETTER than theirs with:
           - 🏆 **[OUR PRODUCT NAME]** - [Our Price]
           - ✅ **Better Because:** [Why our product is better]
           - 💰 **Price Advantage:** [If we're cheaper]
           - ⭐ **Quality Advantage:** [If we have higher rating/better features]

        3. **DIRECT REPLACEMENTS:**
           If they're looking at [Competitor Product], they should buy [Our Product] because...

        4. **RECOMMENDATION SUMMARY:**
           Clear recommendations of which OUR products to choose instead of theirs.

        FORMAT:
        Start with: "🆚 **DIRECT COMPARISON RESULTS**"
        Use simple bullet points
        Focus on WHY OUR PRODUCTS ARE BETTER
        End with clear purchase suggestions
        """
        
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=comparison_prompt
        )
        
        return response.text
        
    except Exception as e:
        return f"Error in comparison: {str(e)}"

def detect_language(text):
    try:
        text_lower = text.lower().strip()
        
        language_patterns = {
            'en': ['hello', 'hi', 'thanks', 'please', 'want', 'price', 'good morning', 'thank you'],
            'it': ['ciao', 'grazie', 'per favore', 'vorrei', 'voglio', 'prezzo', 'buongiorno', 
                  'grazie mille', 'buonasera', 'arrivederci', 'perfetto', 'bello', 'grazie'],
            'fr': ['bonjour', 'merci', 's il vous plaît', 'je voudrais', 'salut', 'merci beaucoup',
                  'bonsoir', 'au revoir', 'parfait', 'beau', 'excusez-moi'],
            'es': ['hola', 'gracias', 'por favor', 'quiero', 'buenos días', 'muchas gracias',
                  'buenas tardes', 'adiós', 'perfecto', 'hermoso', 'disculpe'],
            'de': ['hallo', 'danke', 'bitte', 'ich möchte', 'guten tag', 'vielen dank',
                  'guten abend', 'auf wiedersehen', 'perfekt', 'schön', 'entschuldigung'],
            'pt': ['olá', 'obrigado', 'por favor', 'eu gostaria', 'bom dia', 'muito obrigado',
                  'boa tarde', 'adeus', 'perfeito', 'bonito', 'com licença'],
            'ru': ['привет', 'спасибо', 'пожалуйста', 'я хотел бы', 'добрый день', 'большое спасибо'],
            'ja': ['こんにちは', 'ありがとう', 'お願いします', '欲しい', 'おはよう', 'ありがとうございます'],
            'ko': ['안녕하세요', '감사합니다', '부탁합니다', '원해요', '좋은 아침', '대단히 감사합니다'],
            'zh': ['你好', '谢谢', '请', '我想要', '早上好', '非常感谢'],
            'hi': ['नमस्ते', 'धन्यवाद', 'कृपया', 'मैं चाहता हूं', 'शुभ प्रभात', 'बहुत बहुत धन्यवाद'],
            'ar': ['مرحبا', 'شكرا', 'من فضلك', 'أريد', 'صباح الخير', 'شكرا جزيلا'],
            'tr': ['merhaba', 'teşekkürler', 'lütfen', 'istiyorum', 'günaydın', 'çok teşekkür ederim']
        }
        
        for lang, words in language_patterns.items():
            if any(word in text_lower for word in words):
                return lang
        
        if any(char in text for char in 'áéíóúñ¿¡'):
            return 'es'
        elif any(char in text for char in 'àâäéèêëîïôöùûüç'):
            return 'fr'
        elif any(char in text for char in 'äöüß'):
            return 'de'
        elif any(char in text for char in 'áàâãçéêíóôõú'):
            return 'pt'
        elif any(char in text for char in 'àèéìíòóùú'):
            return 'it'
        elif any(char in text for char in 'ぁ-んァ-ン一-龯'):
            return 'ja'
        elif any(char in text for char in 'ㄱ-ㅎ가-힣'):
            return 'ko'
        elif any(char in text for char in '你好'):
            return 'zh'
        elif any(char in text for char in 'अ-ह'):
            return 'hi'
        elif any(char in text for char in 'ا-ي'):
            return 'ar'
        elif any(char in text for char in 'çğıöşü'):
            return 'tr'
        else:
            return 'en'
            
    except Exception as e:
        st.error(f"Language detection error: {e}")
        return 'en'

def text_to_speech(text, language='en'):
    try:
        clean_text = re.sub(r'\*\*(.*?)\*\*', r'\1', text)
        clean_text = re.sub(r'\*(.*?)\*', r'\1', clean_text)
        clean_text = re.sub(r'`(.*?)`', r'\1', clean_text)
        
        lang_map = {
            'zh': 'zh-cn',
            'ja': 'ja',
            'ko': 'ko',
            'hi': 'hi',
            'ar': 'ar',
            'tr': 'tr',
            'ru': 'ru',
        }
        
        tts_lang = lang_map.get(language, language)
            
        tts = gTTS(text=clean_text, lang=tts_lang, slow=False)
        
        audio_buffer = BytesIO()
        tts.write_to_fp(audio_buffer)
        audio_buffer.seek(0)
        
        return audio_buffer
        
    except Exception as e:
        st.error(f"Text-to-speech error: {e}")
        return None

class STTAudioProcessor(AudioProcessorBase):
    def __init__(self):
        self.chunks = []

    def recv_audio(self, frame: av.AudioFrame):
        pcm = frame.to_ndarray()
        self.chunks.append(pcm)
        return frame

def get_gemini_response(user_input, user_language, is_url_analysis=False, url_content=None, url_metadata=None):
    try:
        if is_url_analysis and url_content:
            response = compare_and_suggest(url_content, url_metadata)
            return response
        else:
            system_prompt = f"""
            You are Ecom Bot, an AI assistant for my online shop.

            Your role is to assist customers in browsing products, providing information, and guiding them through the checkout process.

            Be friendly and helpful in your interactions.

            We offer a variety of products across categories such as Fashion include clothing, Electronics, Beauty & Personal Care, Sports & Outdoors products.

            Feel free to ask customers about their preferences, recommend products, and inform them about any ongoing promotions.

            The Current Product List is limited as below:

            ```{product_list}```

            Make the shopping experience enjoyable and encourage customers to reach out if they have any questions or need assistance.

            CRITICAL INSTRUCTIONS:
            1. You MUST respond in the same language that the user is using. The user is speaking {user_language.upper()}.
            2. DO NOT use markdown formatting like **bold** or *italic* in your responses. Use plain text only.
            3. Be natural and conversational in your responses.
            4. Keep responses concise but helpful.
            5. Never mention that you're switching languages - just respond naturally in the user's language.
            6. If the user speaks multiple languages in one message, respond in the dominant language you detect.
            7. Always suggest specific products from our list.
            8. Response should be in LIST formate.
            9. Response should be in same font and style.
            """

            conversation_history = [system_prompt]
            for msg in st.session_state.chat_history[-6:]:
                if msg['role'] == 'user':
                    conversation_history.append(f"User: {msg['content']}")
                else:
                    conversation_history.append(f"Assistant: {msg['content']}")
            
            conversation_history.append(f"User: {user_input}")
            full_prompt = "\n".join(conversation_history)
            
            response = client.models.generate_content(
                model="gemini-2.5-flash",  
                contents=full_prompt
            )
            
            clean_response = re.sub(r'\*\*(.*?)\*\*', r'\1', response.text)
            clean_response = re.sub(r'\*(.*?)\*', r'\1', clean_response)
            clean_response = re.sub(r'`(.*?)`', r'\1', clean_response)
            
            return clean_response
            
    except Exception as e:
        error_msg = f"I apologize, but I'm having trouble processing your request. Please try again."
        return error_msg

def get_language_name(lang_code):
    language_names = {
        'en': 'English',
        'es': 'Spanish', 
        'fr': 'French',
        'de': 'German',
        'it': 'Italian',
        'pt': 'Portuguese',
        'ru': 'Russian',
        'ja': 'Japanese',
        'ko': 'Korean',
        'zh': 'Chinese',
        'hi': 'Hindi',
        'ar': 'Arabic',
        'tr': 'Turkish',
        'kn': 'Kannada'
    }
    return language_names.get(lang_code, 'Unknown')

def process_message(user_text, input_type="text"):
    if not user_text.strip():
        return
    
    url = extract_url_from_text(user_text)
    
    user_lang = detect_language(user_text)
    language_name = get_language_name(user_lang)
    
    st.session_state.chat_history.append({'role': 'user', 'content': user_text, 'language': user_lang, 'type': input_type})
    
    if url:
        with st.spinner("🔗 Thinking..."):
            url_content, url_metadata = fetch_url_content(url)
            
            if "Error" not in url_content:
                response = get_gemini_response(user_text, user_lang, is_url_analysis=True, 
                                              url_content=url_content, url_metadata=url_metadata)
                
                response = f"🆚 **PRODUCT COMPARISON**\n\n*Comparing our products with: {url}*\n\n{response}"
            else:
                response = f"❌ Unable to fetch URL content: {url_content}"
    else:
        with st.spinner(f"🤖 Thinking..."):
            response = get_gemini_response(user_text, user_lang)
    
    st.session_state.chat_history.append({'role': 'assistant', 'content': response, 'language': user_lang})
    
    with st.spinner("🔊 Generating audio response..."):
        audio_buffer = text_to_speech(response, user_lang)
    
    if audio_buffer:
        st.session_state.last_audio = audio_buffer.getvalue()
        st.session_state.audio_language = language_name

st.set_page_config(
    page_title="E-commerce Assistant",
    page_icon="🌍",
    layout="wide"
)

st.markdown("""
<div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); padding: 20px; border-radius: 10px; color: white;">
    <h1 style="margin: 0; text-align: center;">🛒 E-commerce Assistant</h1>
    <p style="text-align: center; margin: 5px 0;">Compare competitor products with ours & get better suggestions</p>
</div>
""", unsafe_allow_html=True)

col1, col2 = st.columns([1, 2])

with col1:
    st.header("🎤 Input Methods")
    
    st.subheader("Voice Input (Browser Microphone)")
    
    webrtc_ctx = webrtc_streamer(
    key="speech",
    mode=WebRtcMode.SENDONLY,  # pass the enum directly, do NOT use .name
    media_stream_constraints={"audio": True, "video": False},
)

    
    if webrtc_ctx and webrtc_ctx.state.playing:
        if st.button("Stop & Transcribe", use_container_width=True):
            audio = webrtc_ctx.audio_processor.chunks
            if audio:
                audio_np = np.concatenate(audio, axis=0)
    
                # Save WAV file
                import soundfile as sf
                sf.write("temp.wav", audio_np, 48000)
    
                # Send to Gemini STT
                with open("temp.wav", "rb") as f:
                    stt_response = client.audio.transcribe(
                        file=f,
                        model="gemini-2.5-flash"
                    )
    
                text = stt_response.text
                process_message(text, "voice")
                st.rerun()

    
    st.subheader("🔗 Product Comparison")
    
    url_input = st.text_input("Enter product URL:", 
                              placeholder="https://example.com/product-page", 
                              key="url_input")
    
    if st.button("🔄 Compare & Suggest", key="compare_url", use_container_width=True):
        if url_input:
            parsed_url = urlparse(url_input)
            if not all([parsed_url.scheme, parsed_url.netloc]):
                st.error("Please enter a valid URL (e.g., https://example.com)")
            else:
                user_message = f"Compare with this URL: {url_input}"
                process_message(user_message, "url")
                st.rerun()
        else:
            st.warning("Please enter a URL to compare")
    
    
    st.subheader("Text Input")
    user_text = st.text_area("Type Here:", 
                            height=80, 
                            key="text_input",
                            placeholder="Start Chating...")
    
    if st.button("📤 Chat", key="send_text", use_container_width=True) and user_text.strip():
        process_message(user_text.strip(), "text")
        st.rerun()
    
    st.header("💡 Quick Actions")
    quick_col1, quick_col2 = st.columns(2)
    
    with quick_col1:
        if st.button("👗 Fashion", use_container_width=True):
            process_message("Show me fashion items and prices", "text")
            st.rerun()
        if st.button("💄 Beauty & Personal Care", use_container_width=True):
            process_message("Show me beauty and personal care products", "text")
            st.rerun()

    with quick_col2:
        if st.button("🔌 Electronics", use_container_width=True):
            process_message("Show me electronics and gadgets", "text")
            st.rerun()
        if st.button("🏋️ Sports & Outdoors", use_container_width=True):
            process_message("Show me sports and outdoor products", "text")
            st.rerun()
    if st.button("🏠 Home & Kitchen", use_container_width=True):
        process_message("Show me home and kitchen items", "text")
        st.rerun()
    
    if st.button("🗑️ Clear Chat", type="secondary", use_container_width=True):
        st.session_state.chat_history = []
        st.session_state.recognition_result = None
        st.rerun()

with col2:
    st.header("💬 E-Commerce Chat")
    
    chat_container = st.container()
    with chat_container:
        if not st.session_state.chat_history:
            st.markdown("""
            <div style='padding: 20px; background: black; border-radius: 10px;'>
            <h3> 🤖 Welcome to E-commerce Assistant!</h3>
            <hr>
            <ul>
            <li>🎤 Voice input → Text + Audio in your language</li>
            <li>💬 Text input → Text + Audio in your language</li>
            <li>🔗 URL analysis → Compare products + Text + Audio in your language</li>
            </ul>

            <p><strong>🌍 AUTOMATIC LANGUAGE DETECTION:</strong></p>
            <p>Speak, type, or provide URLs - I'll detect and respond in your language!</p>

            <p><strong>🔊 AUDIO OUTPUT FOR EVERYTHING:</strong></p>
            <p>Every response comes with voice audio in your language!</p>

            <p><strong>🔗 URL PRODUCT ANALYSIS:</strong></p>
            <p>Paste any product URL to compare with our inventory and find better deals!</p>
            </div>
            """, unsafe_allow_html=True)
        else:
            for i, msg in enumerate(st.session_state.chat_history):
                lang_name = get_language_name(msg['language'])
                if msg['role'] == 'user':
                    icon = "🎤" if msg.get('type') == "voice" else "🔗" if msg.get('type') == "url" else "💬"
                    st.markdown(f"""
                    <div style='background: black; padding: 10px; border-radius: 10px; margin: 5px 0; border-left: 4px solid #2196f3;'>
                    <strong>{icon} You :</strong> {msg['content']}
                    </div>
                    """, unsafe_allow_html=True)
                else:
                    is_comparison = "COMPARISON" in msg['content'] or "VS" in msg['content'] or "🆚" in msg['content']
                    border_color = "#FF5722" if is_comparison else "#9c27b0"
                    
                    st.markdown(f"""
                    <div style='background: black; padding: 10px; border-radius: 10px; margin: 5px 0; border-left: 4px solid {border_color};'>
                    <div style='color: {'#4CAF50' if is_comparison else '#9C27B0'}; font-weight: bold; margin-bottom: 5px;'>
                    🤖 {'🔄 Comparison Bot' if is_comparison else 'Shopping Assistant'}
                    </div>
                    {msg['content']}
                    </div>
                    """, unsafe_allow_html=True)
                    
                    if i == len(st.session_state.chat_history) - 1 and msg['role'] == 'assistant':
                        if 'last_audio' in st.session_state:
                            st.audio(st.session_state.last_audio, format="audio/wav")
                            st.info(f"🔊 Audio response in {lang_name}")

st.markdown("---")
