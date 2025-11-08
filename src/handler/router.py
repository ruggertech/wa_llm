import logging
import re
from datetime import datetime, timedelta
from enum import Enum

from pydantic import BaseModel, Field
from pydantic_ai import Agent
from sqlmodel import desc, select
from sqlmodel.ext.asyncio.session import AsyncSession
from voyageai.client_async import AsyncClient

from handler.knowledge_base_answers import KnowledgeBaseAnswers
from models import Message, Group
from whatsapp.jid import parse_jid
from utils.chat_text import chat2text
from whatsapp import WhatsAppClient
from .base_handler import BaseHandler

# Creating an object
logger = logging.getLogger(__name__)


class IntentEnum(str, Enum):
    summarize = "summarize"
    ask_question = "ask_question"
    about = "about"
    other = "other"


class Intent(BaseModel):
    intent: IntentEnum = Field(
        description="""The intent of the message.
- summarize: Summarize TODAY's chat messages, or catch up on the chat messages FROM TODAY ONLY. This will trigger the summarization of the chat messages. This is only relevant for queries about TODDAY chat. A query across a broader timespan is classified as ask_question
- ask_question: Ask a question or learn from the collective knowledge of the group. This will trigger the knowledge base to answer the question.
- about: Learn about me(bot) and my capabilities. This will trigger the about section.
- other:  something else. This will trigger the default response."""
    )


def extract_message_count(text: str) -> int | None:
    """Extract the number of messages requested from user's text.
    
    Handles formats like:
    - "summarize last 5 messages"
    - "סכם 3 הודעות"
    - "תן לי סיכום של הודעה אחת"
    
    Returns None if no specific number is found.
    """
    # Hebrew number words
    hebrew_numbers = {
        'אחת': 1, 'אחד': 1,
        'שתיים': 2, 'שניים': 2, 'שנים': 2,
        'שלוש': 3, 'שלושה': 3,
        'ארבע': 4, 'ארבעה': 4,
        'חמש': 5, 'חמישה': 5,
        'שש': 6, 'שישה': 6,
        'שבע': 7, 'שבעה': 7,
        'שמונה': 8, 'שמונה': 8,
        'תשע': 9, 'תשעה': 9,
        'עשר': 10, 'עשרה': 10,
    }
    
    # English number words
    english_numbers = {
        'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5,
        'six': 6, 'seven': 7, 'eight': 8, 'nine': 9, 'ten': 10,
    }
    
    text_lower = text.lower()
    
    # Check for digit numbers (e.g., "5", "10")
    # Exclude phone numbers (numbers with 9+ digits) and @mentions
    digit_match = re.search(r'(?<!@)\b(\d{1,2})\b(?!\d)', text)
    if digit_match:
        num = int(digit_match.group(1))
        # Only return if it's a reasonable message count (1-100)
        if 1 <= num <= 100:
            return num
    
    # Check Hebrew number words
    for word, num in hebrew_numbers.items():
        if word in text:
            return num
    
    # Check English number words
    for word, num in english_numbers.items():
        if word in text_lower:
            return num
    
    return None


class Router(BaseHandler):
    def __init__(
        self,
        session: AsyncSession,
        whatsapp: WhatsAppClient,
        embedding_client: AsyncClient,
    ):
        self.ask_knowledge_base = KnowledgeBaseAnswers(
            session, whatsapp, embedding_client
        )
        super().__init__(session, whatsapp, embedding_client)

    async def __call__(self, message: Message):
        route = await self._route(message.text)
        match route:
            case IntentEnum.summarize:
                await self.summarize(message)
            case IntentEnum.ask_question:
                await self.ask_knowledge_base(message)
            case IntentEnum.about:
                await self.about(message)
            case IntentEnum.other:
                await self.default_response(message)

    async def _route(self, message: str) -> IntentEnum:
        agent = Agent(
            model="anthropic:claude-sonnet-4-5-20250929",
            system_prompt="What is the intent of the message? What does the user want us to help with?",
            output_type=Intent,
        )

        result = await agent.run(message)
        return result.output.intent

    async def summarize(self, message: Message):
        from models import Group
        
        # Extract requested message count from user's text
        requested_count = extract_message_count(message.text)
        
        # Check if this group has community_keys (linked to other groups)
        if message.group and message.group.community_keys:
            # Get all groups with matching community_keys
            community_groups = await message.group.get_related_community_groups(self.session)
            all_groups = [message.group] + list(community_groups)
            
            # Summarize each group
            summaries = []
            for group in all_groups:
                # Skip current group if send_summary_to_self is False
                # (this allows using a group as a "control panel" without including its messages)
                if group.group_jid == message.chat_jid and not group.send_summary_to_self:
                    continue
                    
                time_24_hours_ago = datetime.now() - timedelta(hours=24)
                stmt = (
                    select(Message)
                    .where(Message.chat_jid == group.group_jid)
                    .where(Message.timestamp >= time_24_hours_ago)
                    .order_by(desc(Message.timestamp))
                    .limit(30)
                )
                res = await self.session.exec(stmt)
                group_messages: list[Message] = res.all()
                
                # Limit messages if user specified a number
                if requested_count:
                    group_messages = group_messages[:requested_count]
                    logger.info(f"User requested summary of {requested_count} messages for group {group.group_name}")
                
                if len(group_messages) < 1:  # Need at least one message
                    continue
                
                # Build appropriate system prompt based on whether count was specified
                if requested_count:
                    system_prompt = f"""Summarize EXACTLY the last {requested_count} message(s) from "{group.group_name}" group.
                    
                    - Start by stating this is a summary of "{group.group_name}" group
                    - Summarize ONLY the {requested_count} most recent message(s) - no more, no less
                    - Be specific about what each message said
                    - Keep it short and conversational
                    - Tag users when mentioning them
                    - CRITICAL: You MUST respond in the EXACT same language as the messages. If the messages are in Hebrew, respond ONLY in Hebrew. If the messages are in English, respond in English. Never translate or mix languages.
                    """
                else:
                    system_prompt = f"""Summarize the following group chat messages in a few words.
                    
                    - Start by stating this is a summary of "{group.group_name}" group
                    - Keep it short and conversational
                    - Tag users when mentioning them
                    - CRITICAL: You MUST respond in the EXACT same language as the messages. If the messages are in Hebrew, respond ONLY in Hebrew. If the messages are in English, respond in English. Never translate or mix languages.
                    """
                
                agent = Agent(
                    model="anthropic:claude-sonnet-4-5-20250929",
                    system_prompt=system_prompt,
                    output_type=str,
                )
                
                try:
                    response = await agent.run(chat2text(group_messages))
                    summaries.append(f"📱 *{group.group_name}*:\n{response.output}")
                except Exception as e:
                    logging.error(f"Error summarizing group {group.group_name}: {e}")
            
            # Send all summaries
            if summaries:
                combined_summary = "\n\n".join(summaries)
                await self.send_message(
                    message.chat_jid,
                    combined_summary,
                    in_reply_to=message.message_id,
                )
            else:
                await self.send_message(
                    message.chat_jid,
                    "No recent messages to summarize in the linked groups.",
                    in_reply_to=message.message_id,
                )
        else:
            # Original behavior: summarize only current group
            # Extract requested message count from user's text
            requested_count = extract_message_count(message.text)
            
            time_24_hours_ago = datetime.now() - timedelta(hours=24)
            stmt = (
                select(Message)
                .where(Message.chat_jid == message.chat_jid)
                .where(Message.timestamp >= time_24_hours_ago)
                .order_by(desc(Message.timestamp))
                .limit(30)
            )
            res = await self.session.exec(stmt)
            messages: list[Message] = res.all()
            
            # Limit messages if user specified a number
            if requested_count:
                messages = messages[:requested_count]
                logger.info(f"User requested summary of {requested_count} messages, limiting to that count")
            
            # Build appropriate system prompt based on whether count was specified
            if requested_count:
                system_prompt = f"""Summarize EXACTLY the last {requested_count} message(s) provided.
                
                - You MUST summarize ONLY the {requested_count} most recent message(s) - no more, no less
                - Be specific about what each message said
                - Keep it short and conversational
                - Tag users when mentioning them
                - CRITICAL: You MUST respond in the EXACT same language as the messages. If the messages are in Hebrew, respond ONLY in Hebrew. If the messages are in English, respond in English. Never translate or mix languages.
                """
            else:
                system_prompt = """Summarize the following group chat messages in a few words.
                
                - You MUST state that this is a summary of TODAY's messages. Even if the user asked for a summary of a different time period (in that case, state that you can only summarize today's messages)
                - Always personalize the summary to the user's request
                - Keep it short and conversational
                - Tag users when mentioning them
                - CRITICAL: You MUST respond in the EXACT same language as the messages. If the messages are in Hebrew, respond ONLY in Hebrew. If the messages are in English, respond in English. Never translate or mix languages.
                """

            agent = Agent(
                model="anthropic:claude-sonnet-4-5-20250929",
                system_prompt=system_prompt,
                output_type=str,
            )

            response = await agent.run(
                f"@{parse_jid(message.sender_jid).user}: {message.text}\n\n # History:\n {chat2text(messages)}"
            )
            await self.send_message(
                message.chat_jid,
                response.output,
                in_reply_to=message.message_id,
            )

    async def about(self, message):
        await self.send_message(
            message.chat_jid,
            "I'm an open source bot https://github.com/ilanbenb/wa_llm, I can help you catch up on the chat messages and answer questions based on the group's knowledge.",
            in_reply_to=message.message_id,
        )

    async def default_response(self, message):
        await self.send_message(
            message.chat_jid,
            "I'm sorry, but I dont think this is something I can help with right now 😅.\n I can help catch up on the chat messages or answer questions based on the group's knowledge.",
            in_reply_to=message.message_id,
        )
