"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import type { Square } from "chess.js";
import { Button } from "../components/buttons/BaseButton";
import { API_URL } from "../const";
import { useBoardPublisher } from "../hooks/use-board-publisher";
import type { BoardTheme, PieceSet } from "../components/chessboard";
import StudyExerciseRunner from "./study_exercise_runner";

type Study = { id: string; title: string; description: string; archived: number };
type Chapter = { id: string; study_id: string; title: string; description: string; position: number };
type Source = { id: string; filename: string; record_index: number; valid: number; diagnostics_json: string; version: number; source_group_id: string };
type Position = { id: string; source_id: string; parent_id: string | null; child_index: number; fen: string; move_uci: string | null;
  comment: string; starting_comment: string; nags_json: string; arrows_json: string; squares_json: string; valid: number };
type Exercise = { id: string; position_id: string; current_revision: number; status: string; sibling_group: string | null;
  source_json: string; point_value: number | null;
  specification: Record<string, unknown>; card: { id: string; state: string } | null };
type Link = { id: string; source_position_id: string; target_position_id: string | null; target_exercise_id: string | null; relation: string };
type ChapterPayload = { chapter: Chapter; sources: Source[]; positions: Position[]; exercises: Exercise[]; links: Link[] };
type Preview = { digest: string; records: Array<{ index: number; headers: Record<string, string>; nodes: Array<{ fen: string }>;
  diagnostics: string[]; valid: boolean }> ; existing_versions: Array<{ source_group_id: string; version: number }> };
type ExerciseType = "square_set" | "move_line" | "knight_path" | "choice" | "explanation";

async function api<T>(path: string, method = "GET", body?: unknown): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    method, headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const data = await response.json() as T & { detail?: string };
  if (!response.ok) throw new Error(data.detail ?? `Study service returned HTTP ${response.status}`);
  return data;
}

function squares(input: string) { return [...new Set((input.toLowerCase().match(/[a-h][1-8]/g) ?? []))]; }

export default function StudiesView({ boardTheme, pieceSet }: { boardTheme: BoardTheme; pieceSet: PieceSet }) {
  const [studies, setStudies] = useState<Study[]>([]);
  const [studyId, setStudyId] = useState("");
  const [studyTitle, setStudyTitle] = useState("");
  const [studyDescription, setStudyDescription] = useState("");
  const [chapters, setChapters] = useState<Chapter[]>([]);
  const [chapterId, setChapterId] = useState("");
  const [chapterTitle, setChapterTitle] = useState("");
  const [chapterRename, setChapterRename] = useState("");
  const [content, setContent] = useState<ChapterPayload | null>(null);
  const [positionId, setPositionId] = useState("");
  const [exerciseId, setExerciseId] = useState("");
  const [editingExerciseId, setEditingExerciseId] = useState("");
  const [scheduleDecision, setScheduleDecision] = useState<"reset" | "preserve">("reset");
  const [practiceId, setPracticeId] = useState("");
  const [previewId, setPreviewId] = useState("");
  const [learnId, setLearnId] = useState("");
  const [rawPgn, setRawPgn] = useState("");
  const [filename, setFilename] = useState("Study.pgn");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [selectedRecords, setSelectedRecords] = useState<number[]>([]);
  const [importMode, setImportMode] = useState<"append" | "update" | "copy">("append");
  const [sourceGroupId, setSourceGroupId] = useState("");
  const [exerciseType, setExerciseType] = useState<ExerciseType>("square_set");
  const [prompt, setPrompt] = useState("");
  const [hint, setHint] = useState("");
  const [explanation, setExplanation] = useState("");
  const [criterion, setCriterion] = useState("");
  const [required, setRequired] = useState("");
  const [optional, setOptional] = useState("");
  const [candidateRegion, setCandidateRegion] = useState("");
  const [acceptedLines, setAcceptedLines] = useState("");
  const [moveMode, setMoveMode] = useState<"single" | "stepwise_line">("single");
  const [gradingPolicy, setGradingPolicy] = useState<"reference" | "open_judgment">("reference");
  const [startSquare, setStartSquare] = useState("");
  const [targetSquares, setTargetSquares] = useState("");
  const [maximumHops, setMaximumHops] = useState(2);
  const [hopRule, setHopRule] = useState<"at_most" | "exact">("at_most");
  const [choiceLines, setChoiceLines] = useState("");
  const [correctOptions, setCorrectOptions] = useState("");
  const [rubric, setRubric] = useState("");
  const [siblingGroup, setSiblingGroup] = useState("");
  const [sourceReference, setSourceReference] = useState("");
  const [pointValue, setPointValue] = useState("");
  const [linkTarget, setLinkTarget] = useState("");
  const [linkRelation, setLinkRelation] = useState<"illustrates" | "contrasts" | "follow_up">("illustrates");
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const { setShellBoardForOwner, releaseShellBoardForOwner } = useBoardPublisher();

  const loadStudies = useCallback(async () => {
    try { setStudies((await api<{ studies: Study[] }>("/api/studies")).studies); setError(""); }
    catch (reason) { setError(String(reason)); }
  }, []);
  const loadStudy = useCallback(async (id: string) => {
    try {
      const result = await api<{ study: Study; chapters: Chapter[] }>(`/api/studies/${id}`);
      setChapters(result.chapters); setStudyTitle(result.study.title); setStudyDescription(result.study.description);
      setChapterId((current) => result.chapters.some((item) => item.id === current) ? current : result.chapters[0]?.id ?? "");
      setError("");
    } catch (reason) { setError(String(reason)); }
  }, []);
  const loadChapter = useCallback(async (id: string) => {
    if (!studyId || !id) { setContent(null); return; }
    try {
      const result = await api<ChapterPayload>(`/api/studies/${studyId}/chapters/${id}`);
      setContent(result);
      setChapterRename(result.chapter.title);
      setPositionId((current) => result.positions.some((item) => item.id === current) ? current : result.positions[0]?.id ?? "");
      setError("");
    } catch (reason) { setError(String(reason)); }
  }, [studyId]);
  useEffect(() => { queueMicrotask(() => void loadStudies()); }, [loadStudies]);
  useEffect(() => { if (studyId) queueMicrotask(() => void loadStudy(studyId)); }, [studyId, loadStudy]);
  useEffect(() => { queueMicrotask(() => void loadChapter(chapterId)); }, [chapterId, loadChapter]);

  const selectedPosition = content?.positions.find((item) => item.id === positionId);
  const selectedExercise = content?.exercises.find((item) => item.id === exerciseId);
  const renderPositionChildren = (sourceId: string, parentId: string | null): ReactNode => {
    const children = content?.positions.filter((item) => item.source_id === sourceId && item.parent_id === parentId)
      .sort((left, right) => left.child_index - right.child_index) ?? [];
    if (!children.length) return null;
    return <ul>{children.map((position) => <li key={position.id}>
      <Button onClick={() => setPositionId(position.id)} aria-current={positionId === position.id ? "true" : undefined}>
        {position.move_uci ?? "Root position"} · {position.fen}</Button>
      {position.comment && <span> {position.comment.slice(0, 120)}</span>}
      {renderPositionChildren(sourceId, position.id)}
    </li>)}</ul>;
  };
  useEffect(() => {
    if (!selectedPosition) return;
    setShellBoardForOwner("studies", {
      fen: selectedPosition.fen, theme: boardTheme, pieceSet, orientation: "white",
      interactionMode: exerciseType === "square_set" || exerciseType === "knight_path" ? "select" : "readonly",
      showHint: false, shapes: [], drawnShapes: [], onSquareSelect: (square: Square) => {
        if (exerciseType === "square_set") setRequired((current) => [...new Set([...squares(current), square])].join(" "));
        if (exerciseType === "knight_path") setTargetSquares((current) => [...new Set([...squares(current), square])].join(" "));
      },
    });
    return () => releaseShellBoardForOwner("studies");
  }, [selectedPosition, boardTheme, pieceSet, exerciseType, setShellBoardForOwner, releaseShellBoardForOwner]);

  const createStudy = async () => {
    try { const result = await api<{ id: string }>("/api/studies", "POST", { title: studyTitle, description: studyDescription });
      await loadStudies(); setStudyId(result.id); setNotice("Study created"); }
    catch (reason) { setError(String(reason)); }
  };
  const saveStudy = async () => {
    try { await api(`/api/studies/${studyId}`, "PATCH", { title: studyTitle, description: studyDescription });
      await loadStudies(); setNotice("Study saved"); } catch (reason) { setError(String(reason)); }
  };
  const createChapter = async () => {
    try { const result = await api<{ id: string }>(`/api/studies/${studyId}/chapters`, "POST", { title: chapterTitle });
      await loadStudy(studyId); setChapterId(result.id); setChapterTitle(""); setNotice("Chapter created"); }
    catch (reason) { setError(String(reason)); }
  };
  const importPreview = async () => {
    try { const result = await api<Preview>(`/api/studies/${studyId}/import/preview`, "POST", {
      chapter_id: chapterId, raw_pgn: rawPgn, filename, source_group_id: sourceGroupId || null });
      setPreview(result); setSelectedRecords(result.records.filter((item) => item.valid).map((item) => item.index)); setError(""); }
    catch (reason) { setError(String(reason)); }
  };
  const importCommit = async () => {
    if (!preview) return;
    try { const result = await api<{ idempotent: boolean }>(`/api/studies/${studyId}/import/commit`, "POST", {
      chapter_id: chapterId, raw_pgn: rawPgn, filename, source_group_id: sourceGroupId || null,
      preview_digest: preview.digest, selected_records: selectedRecords, mode: importMode });
      setNotice(result.idempotent ? "Exact import already exists" : "Source records imported; no exercises enrolled");
      setPreview(null); await loadChapter(chapterId); }
    catch (reason) { setError(String(reason)); }
  };
  const makeSpecification = (): Record<string, unknown> => {
    const shared = { type: exerciseType, prompt, hint, explanation };
    if (exerciseType === "square_set") return { ...shared, criterion, required: squares(required), optional: squares(optional),
      candidate_region: candidateRegion ? squares(candidateRegion) : null };
    if (exerciseType === "move_line") return { ...shared, mode: moveMode, grading_policy: gradingPolicy,
      accepted_lines: acceptedLines.split(/\n|;/).map((line) => line.trim().split(/\s+/).filter(Boolean)).filter((line) => line.length) };
    if (exerciseType === "knight_path") return { ...shared, start_square: startSquare, target_squares: squares(targetSquares),
      minimum_hops: hopRule === "exact" ? maximumHops : 1, maximum_hops: maximumHops, hop_rule: hopRule,
      occupancy_rule: "static_non_capturing" };
    if (exerciseType === "choice") return { ...shared,
      options: choiceLines.split("\n").map((line) => line.trim()).filter(Boolean).map((line) => {
        const divider = line.indexOf("|"); return { id: line.slice(0, divider).trim(), text: line.slice(divider + 1).trim() };
      }), correct_option_ids: correctOptions.split(/[\s,]+/).filter(Boolean) };
    return { ...shared, rubric };
  };
  const createExercise = async () => {
    try {
      if (editingExerciseId) {
        const original = content?.exercises.find((item) => item.id === editingExerciseId);
        if (!original) throw new Error("Exercise changed; reload the chapter");
        await api(`/api/studies/${studyId}/exercises/${editingExerciseId}`, "PUT", {
          expected_revision: original.current_revision, specification: makeSpecification(),
          schedule_decision: scheduleDecision, sibling_group: siblingGroup || null,
          source: sourceReference ? { reference: sourceReference } : {}, point_value: pointValue ? Number(pointValue) : null,
        });
        await loadChapter(chapterId); setNotice("Exercise revision saved"); setEditingExerciseId("");
        return;
      }
      const result = await api<{ id: string }>(`/api/studies/${studyId}/exercises`, "POST", {
      position_id: positionId, specification: makeSpecification(), sibling_group: siblingGroup || null,
      source: sourceReference ? { reference: sourceReference } : {}, point_value: pointValue ? Number(pointValue) : null });
      await loadChapter(chapterId); setExerciseId(result.id); setNotice("Draft exercise created; enroll it when ready"); }
    catch (reason) { setError(String(reason)); }
  };
  const editExercise = (exercise: Exercise) => {
    const specification = exercise.specification;
    setEditingExerciseId(exercise.id); setExerciseType(specification.type as ExerciseType);
    setPrompt(String(specification.prompt ?? "")); setHint(String(specification.hint ?? ""));
    setExplanation(String(specification.explanation ?? ""));
    setCriterion(String(specification.criterion ?? ""));
    setRequired(Array.isArray(specification.required) ? specification.required.join(" ") : "");
    setOptional(Array.isArray(specification.optional) ? specification.optional.join(" ") : "");
    setCandidateRegion(Array.isArray(specification.candidate_region) ? specification.candidate_region.join(" ") : "");
    setAcceptedLines(Array.isArray(specification.accepted_lines) ? specification.accepted_lines.map((line) => Array.isArray(line) ? line.join(" ") : "").join("\n") : "");
    setMoveMode(specification.mode === "stepwise_line" ? "stepwise_line" : "single");
    setGradingPolicy(specification.grading_policy === "open_judgment" ? "open_judgment" : "reference");
    setStartSquare(String(specification.start_square ?? ""));
    setTargetSquares(Array.isArray(specification.target_squares) ? specification.target_squares.join(" ") : "");
    setMaximumHops(Number(specification.maximum_hops ?? 2));
    setHopRule(specification.hop_rule === "exact" ? "exact" : "at_most");
    setChoiceLines(Array.isArray(specification.options) ? specification.options.map((option) => {
      const choice = option as { id: string; text: string }; return `${choice.id}|${choice.text}`;
    }).join("\n") : "");
    setCorrectOptions(Array.isArray(specification.correct_option_ids) ? specification.correct_option_ids.join(" ") : "");
    setRubric(String(specification.rubric ?? "")); setSiblingGroup(exercise.sibling_group ?? "");
    try { setSourceReference((JSON.parse(exercise.source_json) as { reference?: string }).reference ?? ""); }
    catch { setSourceReference(""); }
    setPointValue(exercise.point_value === null ? "" : String(exercise.point_value));
    setScheduleDecision("reset");
  };
  const exerciseAction = async (action: "enroll" | "suspend" | "resume" | "archive" | "train-now") => {
    try { await api(`/api/studies/${studyId}/exercises/${exerciseId}/${action}`, "POST");
      await loadChapter(chapterId); setNotice(action === "train-now" ? "Exercise added to today's queue" : `Exercise ${action === "enroll" ? "enrolled" : action + "d"}`); }
    catch (reason) { setError(String(reason)); }
  };
  const addLink = async () => {
    try { await api(`/api/studies/${studyId}/links`, "POST", {
      source_position_id: positionId,
      target_position_id: linkTarget.startsWith("position:") ? linkTarget.slice(9) : null,
      target_exercise_id: linkTarget.startsWith("exercise:") ? linkTarget.slice(9) : null,
      relation: linkRelation }); await loadChapter(chapterId); setNotice("Teaching link added"); }
    catch (reason) { setError(String(reason)); }
  };
  const download = async (format: "native" | "pgn") => {
    try {
      const result = await api<Record<string, unknown>>(`/api/studies/${studyId}/${format === "native" ? "export" : "export.pgn"}`);
      const contents = format === "native" ? JSON.stringify(result, null, 2) : String(result.pgn ?? "");
      const url = URL.createObjectURL(new Blob([contents], { type: format === "native" ? "application/json" : "text/plain" }));
      const anchor = document.createElement("a"); anchor.href = url;
      anchor.download = `${studyTitle || "study"}.${format === "native" ? "tempo-study.json" : "pgn"}`;
      anchor.click(); URL.revokeObjectURL(url);
      if (format === "pgn") setNotice(String(result.warning));
    } catch (reason) { setError(String(reason)); }
  };
  const importBundleFile = async (file: File) => {
    try {
      const bundle = JSON.parse(await file.text()) as Record<string, unknown>;
      const result = await api<{ study_id: string }>("/api/studies/import-bundle", "POST", { bundle, mode: "preserve" });
      await loadStudies(); setStudyId(result.study_id); setNotice("Tempo study content imported without review history");
    } catch (reason) { setError(String(reason)); }
  };

  return <div className="studies-workspace">
    <header><h1>Studies</h1><p>Read and practice authored material. Only enrolled exercises enter daily training.</p></header>
    {error && <p role="alert">{error}</p>}{notice && <p role="status">{notice}</p>}
    <section><h2>Collections</h2>
      <label>Import Tempo study bundle<input type="file" accept=".json,.tempo-study.json,application/json"
        onChange={(event) => { const file = event.target.files?.[0]; if (file) void importBundleFile(file); }} /></label>
      <label>Study<select value={studyId} onChange={(event) => { setStudyId(event.target.value); setContent(null); }}>
        <option value="">New study</option>{studies.map((item) => <option key={item.id} value={item.id}>{item.title}{item.archived ? " (archived)" : ""}</option>)}
      </select></label>
      <label>Title<input value={studyTitle} onChange={(event) => setStudyTitle(event.target.value)} /></label>
      <label>Description<textarea value={studyDescription} onChange={(event) => setStudyDescription(event.target.value)} /></label>
      <Button onClick={() => void (studyId ? saveStudy() : createStudy())}>{studyId ? "Save study" : "Create study"}</Button>
      {studyId && <Button onClick={() => void api(`/api/studies/${studyId}/${studies.find((item) => item.id === studyId)?.archived ? "unarchive" : "archive"}`, "POST")
        .then(() => loadStudies()).catch((reason: unknown) => setError(String(reason)))}>
        {studies.find((item) => item.id === studyId)?.archived ? "Unarchive study" : "Archive study"}</Button>}
      {studyId && <><Button onClick={() => void download("native")}>Export Tempo study</Button>
        <Button onClick={() => void download("pgn")}>Export PGN (loses exercise metadata)</Button></>}
    </section>
    {studyId && <section><h2>Chapters</h2>
      <label>Chapter<select value={chapterId} onChange={(event) => setChapterId(event.target.value)}>
        <option value="">Select chapter</option>{chapters.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
      </select></label>
      <label>New chapter<input value={chapterTitle} onChange={(event) => setChapterTitle(event.target.value)} /></label>
      <Button onClick={() => void createChapter()}>Add chapter</Button>
      {chapterId && <><Button disabled={chapters.findIndex((item) => item.id === chapterId) <= 0} onClick={() => {
        const next = [...chapters]; const index = next.findIndex((item) => item.id === chapterId);
        [next[index - 1], next[index]] = [next[index], next[index - 1]];
        void api(`/api/studies/${studyId}/chapters/order`, "PUT", next.map((item) => item.id)).then(() => loadStudy(studyId)).catch((reason: unknown) => setError(String(reason)));
      }}>Move earlier</Button><Button disabled={chapters.findIndex((item) => item.id === chapterId) >= chapters.length - 1} onClick={() => {
        const next = [...chapters]; const index = next.findIndex((item) => item.id === chapterId);
        [next[index + 1], next[index]] = [next[index], next[index + 1]];
        void api(`/api/studies/${studyId}/chapters/order`, "PUT", next.map((item) => item.id)).then(() => loadStudy(studyId)).catch((reason: unknown) => setError(String(reason)));
      }}>Move later</Button></>}
      {chapterId && <><label>Chapter title<input value={chapterRename} onChange={(event) => setChapterRename(event.target.value)} /></label>
        <Button onClick={() => void api(`/api/studies/${studyId}/chapters/${chapterId}`, "PATCH", { title: chapterRename })
          .then(() => loadStudy(studyId)).then(() => setNotice("Chapter renamed"))
          .catch((reason: unknown) => setError(String(reason)))}>Rename chapter</Button></>}
    </section>}
    {studyId && chapterId && <><section><h2>Import PGN</h2>
      <label>PGN file<input type="file" accept=".pgn,text/plain" onChange={(event) => {
        const file = event.target.files?.[0]; if (!file) return; setFilename(file.name);
        void file.text().then(setRawPgn).catch((reason: unknown) => setError(String(reason)));
      }} /></label>
      <label>Or paste PGN<textarea value={rawPgn} onChange={(event) => setRawPgn(event.target.value)} /></label>
      <Button onClick={() => void importPreview()}>Preview PGN</Button>
      {preview && <div><h3>Import preview</h3>
        {preview.records.map((record) => <label key={record.index}><input type="checkbox"
          checked={selectedRecords.includes(record.index)} onChange={() => setSelectedRecords((current) => current.includes(record.index) ? current.filter((item) => item !== record.index) : [...current, record.index])} />
          Record {record.index + 1}: {record.headers.Event ?? "Untitled"}, {record.nodes.length} positions, {record.valid ? "valid" : "needs inspection"}
          {record.diagnostics.length > 0 && <span role="alert">{record.diagnostics.join("; ")}</span>}</label>)}
        <label>Import handling<select value={importMode} onChange={(event) => setImportMode(event.target.value as "append" | "update" | "copy")}>
          <option value="append">Append as new source</option><option value="update">Update source with a new version</option><option value="copy">Import as a copy</option>
        </select></label>
        {importMode === "update" && <label>Source to update<select value={sourceGroupId} onChange={(event) => setSourceGroupId(event.target.value)}>
          <option value="">Choose existing source</option>
          {[...new Map((content?.sources ?? []).map((source) => [source.source_group_id, source])).values()].map((source) =>
            <option key={source.source_group_id} value={source.source_group_id}>{source.filename} · version {source.version}</option>)}
        </select></label>}
        <Button onClick={() => void importCommit()}>Commit selected records</Button>
        <Button onClick={() => { setPreview(null); setSelectedRecords([]); }}>Cancel import</Button>
      </div>}
    </section>
    {content && <section><h2>Material</h2><div className="study-source-list">
      {content.sources.map((source) => <div key={source.id}><strong>{source.filename}</strong> · record {source.record_index + 1} · version {source.version}
        {!source.valid && <span role="alert">{JSON.parse(source.diagnostics_json).join("; ")}</span>}
        {renderPositionChildren(source.id, null)}</div>)}
      </div>
      {selectedPosition && <article><h3>Selected position</h3><p>{selectedPosition.fen}</p>
        <p>{selectedPosition.starting_comment} {selectedPosition.comment}</p>
        <p>NAGs: {JSON.parse(selectedPosition.nags_json).join(", ") || "none"}</p>
        <p>Arrows and highlighted squares: {selectedPosition.arrows_json} {selectedPosition.squares_json}</p>
        <h4>Related material</h4>{content.links.filter((link) => link.source_position_id === positionId).map((link) => <Button key={link.id}
          onClick={() => { if (link.target_position_id) setPositionId(link.target_position_id); if (link.target_exercise_id) setExerciseId(link.target_exercise_id); }}>
          {link.relation}: {link.target_position_id ?? link.target_exercise_id}</Button>)}
        <label>Link to position or exercise<select value={linkTarget} onChange={(event) => setLinkTarget(event.target.value)}><option value="">Choose</option>
          {content.positions.filter((item) => item.id !== positionId).map((item) => <option key={item.id} value={`position:${item.id}`}>Position · {item.move_uci ?? "Root"}: {item.fen}</option>)}
          {content.exercises.map((item) => <option key={item.id} value={`exercise:${item.id}`}>Exercise · {String(item.specification.prompt)}</option>)}
        </select></label><select value={linkRelation} onChange={(event) => setLinkRelation(event.target.value as typeof linkRelation)}>
          <option value="illustrates">Illustrates</option><option value="contrasts">Contrasts</option><option value="follow_up">Follow up</option>
        </select><Button disabled={!linkTarget} onClick={() => void addLink()}>Add teaching link</Button>
      </article>}
    </section>}
    {selectedPosition && <section><h2>{editingExerciseId ? "Edit exercise revision" : "Author an exercise"}</h2>
      <label>Interaction<select value={exerciseType} onChange={(event) => setExerciseType(event.target.value as ExerciseType)}>
        <option value="square_set">Select squares</option><option value="move_line">Move or line</option>
        <option value="knight_path">Knight route</option><option value="choice">Choices</option><option value="explanation">Explain</option>
      </select></label>
      <label>Question<input value={prompt} onChange={(event) => setPrompt(event.target.value)} /></label>
      <label>Optional hint<input value={hint} onChange={(event) => setHint(event.target.value)} /></label>
      <label>Post-answer explanation<textarea value={explanation} onChange={(event) => setExplanation(event.target.value)} /></label>
      {exerciseType === "square_set" && <><label>Authored criterion<input value={criterion} onChange={(event) => setCriterion(event.target.value)} placeholder="Whose weak squares, in what region, under which definition?" /></label>
        <label>Required squares<input value={required} onChange={(event) => setRequired(event.target.value)} placeholder="e5 f6" /></label>
        <label>Optional squares<input value={optional} onChange={(event) => setOptional(event.target.value)} /></label>
        <label>Candidate region (optional)<input value={candidateRegion} onChange={(event) => setCandidateRegion(event.target.value)} /></label>
        <p>Select squares on the board to add required answers.</p></>}
      {exerciseType === "move_line" && <><label>Mode<select value={moveMode} onChange={(event) => setMoveMode(event.target.value as typeof moveMode)}><option value="single">Single move</option><option value="stepwise_line">Stepwise line</option></select></label>
        <label>Grading<select value={gradingPolicy} onChange={(event) => setGradingPolicy(event.target.value as typeof gradingPolicy)}><option value="reference">Reference recall</option><option value="open_judgment">Open chess judgment</option></select></label>
        <label>Accepted UCI lines; one per row<textarea value={acceptedLines} onChange={(event) => setAcceptedLines(event.target.value)} placeholder="e2e4 e7e5 g1f3" /></label></>}
      {exerciseType === "knight_path" && <><label>Starting knight square<input value={startSquare} onChange={(event) => setStartSquare(event.target.value)} placeholder="g1" /></label>
        <label>Squares attacked by final knight<input value={targetSquares} onChange={(event) => setTargetSquares(event.target.value)} /></label>
        <label>Maximum hops<input type="number" min="0" max="3" value={maximumHops} onChange={(event) => setMaximumHops(Number(event.target.value))} /></label>
        <label>Hop rule<select value={hopRule} onChange={(event) => setHopRule(event.target.value as typeof hopRule)}><option value="at_most">At most</option><option value="exact">Exactly</option></select></label>
        <p>Static non-capturing geometry; other pieces remain fixed. Board selection adds target squares.</p></>}
      {exerciseType === "choice" && <><label>Options, one ID|text per row<textarea value={choiceLines} onChange={(event) => setChoiceLines(event.target.value)} placeholder="yes|Yes, a threat exists&#10;no|No concrete threat" /></label>
        <label>Correct option IDs<input value={correctOptions} onChange={(event) => setCorrectOptions(event.target.value)} placeholder="no" /></label></>}
      {exerciseType === "explanation" && <label>Rubric shown after response<textarea value={rubric} onChange={(event) => setRubric(event.target.value)} /></label>}
      <label>Sibling group (optional)<input value={siblingGroup} onChange={(event) => setSiblingGroup(event.target.value)} /></label>
      <label>Book/source reference (optional)<input value={sourceReference} onChange={(event) => setSourceReference(event.target.value)} /></label>
      <label>Book points (optional)<input type="number" min="0" value={pointValue} onChange={(event) => setPointValue(event.target.value)} /></label>
      {editingExerciseId && <label>For assessment changes<select value={scheduleDecision}
        onChange={(event) => setScheduleDecision(event.target.value as typeof scheduleDecision)}>
        <option value="reset">Reset schedule (default)</option><option value="preserve">Preserve schedule</option>
      </select></label>}
      <Button onClick={() => void createExercise()}>{editingExerciseId ? "Save revision" : "Create draft exercise"}</Button>
      {editingExerciseId && <Button onClick={() => setEditingExerciseId("")}>Cancel edit</Button>}
    </section>}
    {content && <section><h2>Exercises</h2>
      {content.exercises.filter((item) => item.position_id === positionId).map((item) => <Button key={item.id}
        onClick={() => { setExerciseId(item.id); setPracticeId(""); setPreviewId(""); setLearnId(""); }} aria-current={exerciseId === item.id ? "true" : undefined}>
        {String(item.specification.prompt)} · {item.status} {item.card ? "· enrolled" : ""}</Button>)}
      {selectedExercise && <div><h3>{String(selectedExercise.specification.prompt)}</h3>
        <p>Revision {selectedExercise.current_revision} · {selectedExercise.status} · {selectedExercise.card ? "Enrolled" : "Not enrolled"}</p>
        <Button onClick={() => { setPreviewId(exerciseId); setPracticeId(""); }}>Preview learner prompt</Button>
        <Button onClick={() => { setLearnId(exerciseId); setPracticeId(""); setPreviewId(""); }}>Learn</Button>
        <Button onClick={() => editExercise(selectedExercise)}>Edit exercise</Button>
        <Button onClick={() => { setPracticeId(exerciseId); setPreviewId(""); }}>Practice without scheduling</Button>
        {!selectedExercise.card && <Button onClick={() => void exerciseAction("enroll")}>Enroll in daily queue</Button>}
        {selectedExercise.card && <><Button onClick={() => void exerciseAction("suspend")}>Suspend</Button>
          <Button onClick={() => void exerciseAction("resume")}>Resume</Button>
          <Button onClick={() => void exerciseAction("train-now")}>Train now</Button></>}
        <Button onClick={() => void exerciseAction("archive")}>Archive</Button>
        {previewId === exerciseId && <div aria-label="Learner preview"><h4>Scheduled learner preview</h4><p>{String(selectedExercise.specification.prompt)}</p>
          <p>Answer and source comments remain hidden until response commitment.</p></div>}
        {learnId === exerciseId && <div aria-label="Study lesson"><h4>Learn this position</h4>
          <p>{selectedPosition?.fen}</p><p>{selectedPosition?.starting_comment} {selectedPosition?.comment}</p>
          <p>{String(selectedExercise.specification.explanation ?? "")}</p>
          <p>Study this material freely. Learning here does not change your schedule.</p></div>}
        {practiceId === exerciseId && <StudyExerciseRunner studyId={studyId} exerciseId={exerciseId} boardTheme={boardTheme} pieceSet={pieceSet} />}
      </div>}
    </section>}
    </>}
  </div>;
}
