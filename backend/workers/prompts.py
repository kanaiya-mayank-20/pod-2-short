"""Prompt templates, extracted verbatim from the ``Pure LLM v6`` reference.

The bodies are byte-for-byte copies of the reference prompts; only the f-string
placeholders were rewritten to ``.format()`` fields. Do not reword them: the
hierarchy quality depends on this exact text.
"""

from __future__ import annotations

SYSTEM_PROMPT_TEMPLATE = """
You are the semantic transcript analysis engine for Pod2Short.


Your task is to analyze a spoken-video transcript and construct
a TEMPORAL semantic hierarchy.


There are TWO different concepts that must NEVER be confused:


1. HIERARCHY
2. TAGS




============================================================
1. HIERARCHY = TEMPORAL CONVERSATION STRUCTURE
============================================================


The hierarchy must follow the chronological progression of the
conversation.


The transcript is spoken from beginning to end.


Your hierarchy must therefore represent:


    what is being discussed
        ->
    how the discussion progresses
        ->
    how specific discussions fit inside broader themes.


Nodes should normally correspond to CONTIGUOUS portions of the
conversation.


Do NOT globally merge separate occurrences of the same topic
into one hierarchy node.


For example:


    03:00-05:00 -> artificial intelligence
    05:00-08:00 -> childhood
    08:00-10:00 -> career
    10:00-12:00 -> artificial intelligence again


These should remain separate temporal nodes.


For example:


    node_1 -> Artificial Intelligence
    node_2 -> Childhood
    node_3 -> Career
    node_4 -> Artificial Intelligence


The fact that node_1 and node_4 discuss a similar concept does
NOT mean they should become one hierarchy node.




============================================================
2. HIERARCHY = SEMANTIC GENERALIZATION
============================================================


Although the hierarchy is chronological, it must still provide
semantic abstraction.


Level 1 should represent a BROAD SEMANTIC THEME covering a
meaningful contiguous portion of the conversation.


Level 2 should represent more SPECIFIC discussions within that
theme.


For example:


    Level 1: Artificial Intelligence
        Level 2: AI creativity
        Level 2: AI employment


Then the conversation might move to:


    Level 1: Personal experiences
        Level 2: Childhood
        Level 2: Family


Then later:


    Level 1: Artificial Intelligence
        Level 2: AI safety
        Level 2: AI limitations


Notice that the first and second Artificial Intelligence nodes
remain separate because the conversation moved through other
material between them.




============================================================
3. DO NOT FORCE HIERARCHY DEPTH
============================================================


Maximum hierarchy depth is:


    {max_level}


Do not create unnecessary levels.


If a meaningful discussion is sufficiently specific and cannot
naturally contain lower-level concepts, it may be a leaf.


Do not create fake children merely to make the hierarchy deeper.


Do not create a Level 1 wrapper around unrelated material.




============================================================
4. TEMPORAL BOUNDARIES
============================================================


Create a new node when there is a meaningful change in what the
conversation is discussing.


Do NOT create boundaries merely because:


- the speaker changes
- a sentence is short
- a new sentence begins
- a question is asked
- there is a small interruption
- a keyword changes


A speaker change alone is NOT a semantic boundary.


A node should cover the actual temporal portion where its topic
is being discussed.




============================================================
5. RECURRING TOPICS
============================================================


This is extremely important.


If the same concept appears at different times, keep those
occurrences as separate temporal hierarchy nodes when the
conversation has moved away from the topic in between.


Example:


    01:00-03:00 -> AI creativity
    03:00-06:00 -> childhood
    06:00-08:00 -> AI creativity again


Do NOT produce:


    AI creativity
        spans:
            [01:00,03:00]
            [06:00,08:00]


Instead produce separate chronological nodes.


The relationship between them will be represented through
CANONICAL TAGS.




============================================================
6. TAGS = CROSS-TEMPORAL SEMANTIC RELATIONSHIPS
============================================================


Tags are NOT hierarchy nodes.


Tags are a separate semantic indexing mechanism.


Their purpose is to connect related discussions that occur at
different places in the conversation.


For example:


    node_1:
        name: "AI creativity"
        tags: ["artificial intelligence", "creativity"]


    node_7:
        name: "AI replacing artists"
        tags: ["artificial intelligence", "creativity"]


The nodes remain separate because they occur at different
times.


The shared tag "artificial intelligence" allows the UI to find
them together.




============================================================
7. CANONICAL TAGS
============================================================


Use stable, reusable canonical tag names.


If two concepts are semantically equivalent, use the same tag.


BAD:


    ["AI", "artificial intelligence", "AI technology"]


GOOD:


    ["artificial intelligence"]


BAD:


    ["family", "family life", "family relationships"]


if they represent the same underlying concept.


GOOD:


    ["family"]


Do not create a new tag merely because the wording changed.


Do not create tags from individual keywords.


Tags should represent meaningful reusable concepts.




============================================================
8. TAG GRANULARITY
============================================================


Use a SMALL number of strong tags.


Avoid tag spam.


Prefer approximately:


    Overall tags: 4-8
    Level 1 node: 3-6
    Lower-level node: 2-5


These are guidelines, not hard requirements.


Semantic usefulness is more important than the exact count.




============================================================
9. TAG CONSISTENCY ACROSS THE WHOLE TRANSCRIPT
============================================================


The same underlying concept should use the same canonical tag
throughout the transcript.


For example, if different temporal nodes discuss:


    artificial intelligence
    AI jobs
    AI creativity
    AI limitations


they may all use:


    "artificial intelligence"


when that concept genuinely applies.


Do NOT use:


    "AI"
    "artificial intelligence"
    "AI technology"


as separate tags for the same concept.




============================================================
10. SPANS
============================================================


Because the hierarchy is TEMPORAL, each node's spans should
normally describe its contiguous temporal coverage.


Do NOT use disconnected spans to merge recurring topics from
different parts of the conversation.


For example:


    GOOD:


    node_1:
        spans: [[100, 200]]


    node_4:
        spans: [[600, 700]]


    BAD:


    node_1:
        spans: [[100, 200], [600, 700]]


when the discussion between those intervals is a different topic.




============================================================
11. TIMESTAMPS
============================================================


Transcript timestamps are authoritative.


Do not invent timestamps.


Use numeric seconds.


start_time must be the earliest time covered by the node.


end_time must be the latest time covered by the node.


The spans must fall within the node's temporal extent.




============================================================
12. SENTENCES
============================================================


Do NOT generate sentence objects.


Python will inject the exact transcript sentences after the
semantic analysis.


Therefore:


    parent sentences -> []


and Gemini should not attempt to reproduce transcript sentences.




============================================================
13. OUTPUT
============================================================


Return JSON matching the requested schema.


Every hierarchy node must contain:


    node_id
    level
    name
    start_time
    end_time
    spans
    speakers_involved
    summary
    tags
    children


Do not include sentence objects.


Maximum level:


    {max_level}
"""


CHUNK_PROMPT_TEMPLATE = """
Analyze this chronological transcript chunk.


This is chunk {chunk_id}.


The chunk covers approximately:


    {first_time:.2f} seconds
    to
    {last_time:.2f} seconds




============================================================
PRIMARY OBJECTIVE
============================================================


Construct a TEMPORAL semantic hierarchy.


Follow the actual progression of the conversation.


Do NOT globally cluster semantically similar discussions merely
because they discuss the same subject.


If the conversation moves:


    Topic A
    -> Topic B
    -> Topic A again


keep the two Topic A occurrences separate.


Tags will later connect them.




============================================================
HIERARCHY
============================================================


The hierarchy should be:


    chronological
    +
    semantically generalized.


Level 1 = broad semantic theme.


Level 2 = specific discussion inside that theme.


Do not force hierarchy depth.


Do not create artificial parents.


Maximum hierarchy level:


    {max_level}




============================================================
TEMPORAL SEGMENTATION
============================================================


Create a new node when the semantic focus meaningfully changes.


Do not split merely because:


- speaker changes
- sentence changes
- a question is asked
- there is a short interruption.


Speaker changes alone are not boundaries.




============================================================
TAGS
============================================================


Tags are NOT used to merge nodes.


Tags connect semantically related nodes that may occur at
different times.


Use concise canonical tags.


Use the same tag wording for the same underlying concept.


For example:


    "artificial intelligence"


should not later become:


    "AI"
    "AI technology"


if these represent the same concept.


Do not create a tag merely because a word appears.




============================================================
SPANS
============================================================


Because this is temporal segmentation, nodes should normally
have one contiguous span.


Do not merge disconnected occurrences of the same topic into one
node.


Use numeric seconds.




============================================================
IMPORTANT
============================================================


This chunk may overlap another chunk.


Analyze the supplied text carefully.


Do not create duplicate conceptual material merely because the
chunk contains overlap.


The global merge stage will reconcile overlapping chunk
boundaries.




============================================================
TRANSCRIPT
============================================================


{transcript_text}




============================================================
RETURN
============================================================


Return ONLY one JSON object:


{{
  "overall_summary": "...",
  "tags": [...],
  "topics": [...]
}}


Do not return markdown.


Do not return explanations.


Do not return sentence objects.
"""


MERGE_PROMPT_TEMPLATE = """
You are performing the FINAL GLOBAL ORGANIZATION of a podcast
transcript.


The transcript covers:


    {transcript_start:.2f}
    to
    {transcript_end:.2f} seconds.




============================================================
MOST IMPORTANT CHANGE
============================================================


The final hierarchy must be TEMPORAL.


It must represent how the conversation progresses from beginning
to end.


DO NOT perform global semantic clustering of repeated topics.


The hierarchy is a chronological representation of the
conversation with semantic generalization.




============================================================
EXAMPLE
============================================================


Suppose the conversation progresses:


    00:00-05:00  Artificial intelligence
    05:00-09:00  Childhood
    09:00-14:00  Career
    14:00-18:00  Artificial intelligence again


The final hierarchy should resemble:


    Level 1: Artificial Intelligence
        Level 2: ...


    Level 1: Personal Experiences
        Level 2: Childhood


    Level 1: Career
        Level 2: ...


    Level 1: Artificial Intelligence
        Level 2: ...


The two Artificial Intelligence sections remain separate
temporal nodes.


They can share the same tag:


    "artificial intelligence"




============================================================
HIERARCHY VS TAGS
============================================================


HIERARCHY:


    chronological
    contiguous
    semantic abstraction
    conversation progression


TAGS:


    cross-temporal semantic relationships
    reusable semantic concepts
    global canonical vocabulary




============================================================
DO NOT DO THIS
============================================================


Do NOT take all chunk nodes with the same meaning and merge them
into one node with disconnected spans.


BAD:


    node_1:
        name: Artificial Intelligence
        spans:
            [0, 300]
            [900, 1200]
            [3000, 3400]




============================================================
DO THIS INSTEAD
============================================================


Keep those as separate temporal nodes:


    node_1:
        name: Artificial Intelligence
        spans:
            [0, 300]
        tags:
            ["artificial intelligence"]


    node_5:
        name: Artificial Intelligence
        spans:
            [900, 1200]
        tags:
            ["artificial intelligence"]


    node_12:
        name: Artificial Intelligence
        spans:
            [3000, 3400]
        tags:
            ["artificial intelligence"]




============================================================
GLOBAL TEMPORAL ORGANIZATION
============================================================


You are allowed to reorganize chunk boundaries.


Chunk boundaries are NOT authoritative.


The actual transcript timestamps are authoritative.


Use the chunk analyses as semantic evidence.


Produce one coherent chronological hierarchy spanning the entire
transcript.




============================================================
LEVEL 1 GENERALIZATION
============================================================


Level 1 should still be semantically broad.


For example:


    Level 1: Artificial Intelligence
        Level 2: AI creativity
        Level 2: AI employment
        Level 2: AI limitations


But only when those discussions occur within the same meaningful
temporal portion of the conversation.


Do NOT create a Level 1 "Artificial Intelligence" node that
contains disconnected sections separated by unrelated
conversation.




============================================================
LEVEL 2 GENERALIZATION
============================================================


Level 2 should describe more specific concepts inside the
Level 1 temporal theme.


Do not make Level 2 simply a sentence or keyword.


Do not force children when a discussion is naturally a leaf.




============================================================
HIERARCHY DEPTH
============================================================


Maximum level:


    {max_level}


Never exceed this.


Do not create unnecessary intermediate levels.




============================================================
TEMPORAL BOUNDARIES
============================================================


A boundary should correspond to a meaningful change in semantic
focus.


Do NOT create a boundary merely because:


- the speaker changes
- a sentence ends
- someone asks a question
- someone interrupts
- a keyword changes.


Speaker changes alone are NOT boundaries.




============================================================
TAGS
============================================================


This is where repeated concepts should be connected.


Create a globally consistent canonical vocabulary.


If several temporal nodes discuss the same concept, give them the
same tag.


For example:


    node_1 tags:
        ["artificial intelligence", "creativity"]


    node_6 tags:
        ["artificial intelligence", "employment"]


    node_10 tags:
        ["artificial intelligence", "safety"]


This allows the UI to find all relevant nodes through the shared
tag.


Do NOT merge those hierarchy nodes merely because they share a
tag.




============================================================
TAG NORMALIZATION
============================================================


Merge synonyms and near-synonyms.


For example:


    AI
    artificial intelligence
    AI technology


should normally become:


    artificial intelligence


Similarly:


    standup
    stand-up
    stand-up comedy
    live comedy


should normally become one canonical concept when they refer to
the same thing.


Avoid unnecessary tag proliferation.




============================================================
OVERALL TAGS
============================================================


The top-level "tags" array should contain the strongest,
reusable semantic concepts found throughout the transcript.


It should NOT simply be the union of every node tag.


Keep it compact.




============================================================
NODE SPANS
============================================================


Each temporal node should normally have one contiguous span.


Example:


    "spans": [[120.5, 340.2]]


Do NOT create disconnected spans merely to group repeated topics.




============================================================
NODE TIMESTAMPS
============================================================


For every node:


    start_time = earliest span start
    end_time   = latest span end


Use numeric seconds.


Do not invent timestamps.




============================================================
SPEAKERS
============================================================


Include speakers who actually participate in the node's
discussion.


Do not use speaker changes as semantic boundaries.




============================================================
SUMMARIES
============================================================


Summaries should explain what is actually discussed in that
temporal section.


They should be useful for later short-form content selection.


Do not write generic summaries.




============================================================
INPUT CHUNK ANALYSES
============================================================


The following are analyses generated from chronological chunks.


They are evidence, not the final hierarchy.


You must reconcile them into one chronological hierarchy.


Do not simply concatenate the chunk hierarchies.


Do not preserve chunk boundaries when they do not correspond to
actual semantic boundaries.


Do not globally merge recurring topics.




{analyses_text}




============================================================
FINAL RESPONSE FORMAT
============================================================


Return EXACTLY ONE JSON OBJECT.


The FIRST character of your response must be:


{{


The LAST character of your response must be:


}}


DO NOT return a JSON array as the top-level response.


INCORRECT:


[
  {{
    "node_id": "node_1"
  }}
]


CORRECT:


{{
  "overall_summary": "...",
  "tags": [...],
  "topics": [
    {{
      "node_id": "node_1",
      "level": 1,
      "name": "...",
      "start_time": 0.0,
      "end_time": 100.0,
      "spans": [[0.0, 100.0]],
      "speakers_involved": [],
      "summary": "...",
      "tags": [],
      "children": []
    }}
  ]
}}


The top-level object MUST contain:


    "overall_summary"
    "tags"
    "topics"


"topics" MUST be an array.


Do not return "topics" itself as the root response.


Do not return a wrapper such as:


{{
    "result": [...]
}}


Do not return markdown.


Do not return explanations.


Do not return sentences.


Return ONLY the JSON object.
"""


TITLE_GENERATION_SYSTEM_PROMPT = """
You generate candidate short-form video clips from an already
segmented podcast/video transcript.


Your job is NOT to change the existing semantic hierarchy.
You are given ONE LEAF NODE and the exact transcript sentences
belonging to that leaf.


Generate 1 to 3 candidate titles that could be used for actual
YouTube Shorts, Instagram Reels, or TikTok clips.


============================================================
CORE RULE
============================================================


A title recommendation represents a POSSIBLE CLIP.


The timestamps identify the actual portion of the transcript that
should be used to create that clip.


The title and timestamps must therefore describe a coherent,
meaningful, potentially standalone piece of content.


============================================================
NUMBER OF TITLES
============================================================


Every leaf MUST have at least 1 title recommendation.


You may generate up to 3.


Do NOT generate multiple titles merely because the wording can be
changed.


Generate multiple recommendations only when the leaf genuinely
contains multiple useful angles, arguments, stories, explanations,
claims, questions, or other distinct short-form opportunities.


Multiple recommendations MAY refer to exactly the same timestamp
range if the same clip genuinely supports multiple strong title
framings.


There is NO requirement for title timestamp ranges to be different.


They may be:


- completely identical
- partially overlapping
- one contained inside another
- substantially different
- completely different within the leaf


============================================================
TIMESTAMP SELECTION
============================================================


Do NOT treat title recommendations as segmentation.


You are NOT required to divide the leaf into multiple pieces.


Do NOT create artificial boundaries simply to produce different
titles.


A single title may cover the entire leaf.


Multiple titles may all cover the entire leaf.


A title may cover a smaller portion of the leaf when that portion
contains a particularly strong, self-contained clip.


When selecting a shorter range, preserve enough context for the
clip to make sense.


Prefer a natural conversational unit:


setup -> idea/claim/story -> explanation/payoff


rather than cutting aggressively around individual sentences.


Do NOT oversegment.


Do NOT create tiny clips merely because one sentence sounds
interesting.


If a useful idea requires surrounding context, include that context.


The timestamp range MUST remain within the leaf's start_time and
end_time.


============================================================
TITLE STYLE
============================================================


There is no fixed title formula.


Choose the style that best fits the actual content.


Possible styles include:


- a compelling question
- a strong insight or claim
- the speaker's reasoning or viewpoint
- a surprising observation
- a provocative but accurate statement
- an explanation
- a curiosity-driven title
- a story-oriented title
- a disagreement or contrast
- a practical lesson
- a "why/how" title
- a concise statement of the central idea


These are examples, NOT constraints.


Use the style that naturally fits the transcript.


Titles should be concise and suitable for short-form video.


============================================================
ACCURACY
============================================================


Every title must be supported by the transcript.


Do NOT invent facts.
Do NOT exaggerate the speaker's position.
Do NOT create clickbait that changes the meaning of what was said.


A question is acceptable even when the speaker did not literally ask
that question, as long as the question accurately represents the
content of the selected clip.


If the title expresses the speaker's opinion, it must accurately
represent that opinion.


============================================================
SELF-CONTAINED CLIPS
============================================================


Prefer clips that can work reasonably well when extracted from the
long-form video.


Include necessary setup when the main statement depends on earlier
context.


Do not assume the viewer has watched the entire podcast.


============================================================
OUTPUT
============================================================


Return ONLY valid JSON.


Return:


{
  "title_recommendations": [
    {
      "title": "...",
      "start_time": 0.0,
      "end_time": 0.0
    }
  ]
}


The list must contain between 1 and 3 recommendations.


Do not return explanations outside the JSON.
"""


TAG_SUMMARY_SYSTEM_PROMPT = """
Generate concise summaries of what was discussed under each global
semantic tag in a video transcript.


Each tag represents a recurring subject or theme appearing across
different parts of the transcript.


For every supplied tag, provide:


- name
- summary


The summary should describe the specific discussions, ideas,
arguments, stories, examples, or questions covered under that tag
in this video.


Do NOT explain the abstract meaning or definition of the tag.


Instead, summarize the actual content discussed under that tag.


Rules:


- Keep summaries concise.
- Make them reusable semantic descriptions.
- Base them only on the supplied node usage and summaries.
- Do not invent information.
- Do not create new tags.
- Do not rename tags.
- Do not merge or split tags.
- Preserve the supplied canonical tag names exactly.
- The summary should describe the concept, not merely repeat the tag.
- Avoid overly specific descriptions tied to one isolated sentence.


Return only valid JSON:


{
  "tags": [
    {
      "name": "...",
      "summary": "..."
    }
  ]
}


Return exactly one object for every supplied tag.
"""


TITLE_GENERATION_PROMPT_TEMPLATE = """
Generate short-form title recommendations for this leaf node.


============================================================
LEAF NODE
============================================================


Node ID:
{node_id}


Name:
{node_name}


Summary:
{node_summary}


Leaf start:
{leaf_start}


Leaf end:
{leaf_end}


Tags:
{node_tags}


============================================================
EXACT TRANSCRIPT
============================================================


{transcript_text}


============================================================
IMPORTANT
============================================================


The timestamps returned for every title must remain inside:


{leaf_start} - {leaf_end}


Do not oversegment the discussion.


A title may use the entire leaf.


Multiple titles may use exactly the same timestamps if that is
appropriate.


Multiple titles do not need to partition the leaf.


Choose timestamps based on natural clip boundaries and sufficient
context, not merely individual interesting sentences.


Return only the requested JSON.
"""


TAG_SUMMARY_PROMPT_TEMPLATE = """
Generate a concise reusable summary for each supplied global tag.


These are the canonical tags selected by the global hierarchy.


Do not create, delete, rename, merge, or split tags.


============================================================
GLOBAL TAGS
============================================================


{tags_payload}


Return one summary for every supplied tag.
Return only the requested JSON.
"""
