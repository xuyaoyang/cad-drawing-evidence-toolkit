using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Text;
using System.Text.RegularExpressions;
using ZwSoft.ZwCAD.ApplicationServices;
using ZwSoft.ZwCAD.DatabaseServices;
using ZwSoft.ZwCAD.EditorInput;
using ZwSoft.ZwCAD.Geometry;
using ZwSoft.ZwCAD.Runtime;

namespace CadDeepeningAssistance
{
    /// <summary>
    /// Reads a target DWG as a side database. The target is never opened as an
    /// MDI document, so graphics regeneration, view restoration and document
    /// activation are bypassed. Only top-level layout/model-space objects are
    /// indexed; nested definitions remain unresolved by design.
    /// </summary>
    public sealed class SideDatabaseIndexExporterD2L
    {
        [CommandMethod("CADSIDEDBINDEXD2L")]
        public void Export()
        {
            Document host = Application.DocumentManager.MdiActiveDocument;
            Editor editor = host == null ? null : host.Editor;
            string input = Environment.GetEnvironmentVariable(
                "CAD_D2L_SIDEDB_INPUT");
            string outputDirectory = Environment.GetEnvironmentVariable(
                "CAD_D2L_SIDEDB_OUTPUT");
            if (String.IsNullOrWhiteSpace(input) ||
                !File.Exists(input) ||
                String.IsNullOrWhiteSpace(outputDirectory))
            {
                if (editor != null)
                {
                    editor.WriteMessage(
                        "\nCADSIDEDBINDEXD2L: input/output environment "
                        + "variables are invalid.");
                }
                return;
            }

            input = Path.GetFullPath(input);
            outputDirectory = Path.GetFullPath(outputDirectory);
            Directory.CreateDirectory(outputDirectory);
            string stem = Path.GetFileNameWithoutExtension(input);
            string phasePath = Path.Combine(
                outputDirectory,
                stem + ".cad_side_database_index_d2l.phase.json");
            string outputPath = Path.Combine(
                outputDirectory,
                stem + ".cad_side_database_index_d2l.json");
            int maxEntities = ReadPositiveInteger(
                Environment.GetEnvironmentVariable(
                    "CAD_D2L_MAX_TOP_LEVEL_ENTITIES"),
                200000);
            int maxExpandedEntities = ReadPositiveInteger(
                Environment.GetEnvironmentVariable(
                    "CAD_D2L_MAX_EXPANDED_ENTITIES"),
                500000);
            string expandPattern = Environment.GetEnvironmentVariable(
                "CAD_D2L_EXPAND_BLOCK_REGEX");
            Regex expandRegex = String.IsNullOrWhiteSpace(expandPattern)
                ? null
                : new Regex(
                    expandPattern,
                    RegexOptions.IgnoreCase |
                    RegexOptions.CultureInvariant);
            string expandRootHandlePattern =
                Environment.GetEnvironmentVariable(
                    "CAD_D2L_EXPAND_ROOT_HANDLE_REGEX");
            Regex expandRootHandleRegex =
                String.IsNullOrWhiteSpace(expandRootHandlePattern)
                ? null
                : new Regex(
                    expandRootHandlePattern,
                    RegexOptions.IgnoreCase |
                    RegexOptions.CultureInvariant);
            string explodeTopLevelHandlePattern =
                Environment.GetEnvironmentVariable(
                    "CAD_D2L_EXPLODE_TOP_LEVEL_HANDLE_REGEX");
            Regex explodeTopLevelHandleRegex =
                String.IsNullOrWhiteSpace(explodeTopLevelHandlePattern)
                ? null
                : new Regex(
                    explodeTopLevelHandlePattern,
                    RegexOptions.IgnoreCase |
                    RegexOptions.CultureInvariant);
            var timer = Stopwatch.StartNew();
            WritePhase(phasePath, "command_started", timer.Elapsed.TotalSeconds);

            var records = new List<Record>();
            var counters = new Counters();
            try
            {
                using (Database database = new Database(false, true))
                {
                    database.ReadDwgFile(
                        input,
                        FileOpenMode.OpenForReadAndAllShare,
                        false,
                        String.Empty);
                    WritePhase(
                        phasePath,
                        "database_read",
                        timer.Elapsed.TotalSeconds);
                    using (Transaction transaction =
                        database.TransactionManager.StartTransaction())
                    {
                        BlockTable table = (BlockTable)transaction.GetObject(
                            database.BlockTableId, OpenMode.ForRead);
                        foreach (ObjectId blockId in table)
                        {
                            BlockTableRecord space = null;
                            try
                            {
                                space = transaction.GetObject(
                                    blockId,
                                    OpenMode.ForRead) as BlockTableRecord;
                            }
                            catch (System.Exception)
                            {
                                counters.Skipped++;
                            }
                            if (space == null || !space.IsLayout ||
                                space.IsFromExternalReference)
                            {
                                continue;
                            }
                            counters.LayoutSpaces++;
                            foreach (ObjectId entityId in space)
                            {
                                if (counters.TopLevelEntities >= maxEntities)
                                {
                                    counters.Truncated = true;
                                    break;
                                }
                                try
                                {
                                    Entity entity = transaction.GetObject(
                                        entityId,
                                        OpenMode.ForRead) as Entity;
                                    if (entity == null) continue;
                                    counters.TopLevelEntities++;
                                    DBText text = entity as DBText;
                                    if (text != null)
                                    {
                                        Record textRecord = Record.ForText(
                                            "DBText",
                                            text.TextString,
                                            text.Position,
                                            text.Rotation,
                                            "entity-world",
                                            text.Layer,
                                            space.Name,
                                            text.Handle.ToString());
                                        ApplyVisibility(
                                            transaction, text, textRecord,
                                            true, true);
                                        records.Add(textRecord);
                                        counters.DirectText++;
                                        continue;
                                    }
                                    MText mtext = entity as MText;
                                    if (mtext != null)
                                    {
                                        Record textRecord = Record.ForText(
                                            "MText",
                                            mtext.Contents,
                                            mtext.Location,
                                            mtext.Rotation,
                                            "entity-world",
                                            mtext.Layer,
                                            space.Name,
                                            mtext.Handle.ToString());
                                        ApplyVisibility(
                                            transaction, mtext, textRecord,
                                            true, true);
                                        records.Add(textRecord);
                                        counters.DirectText++;
                                        continue;
                                    }
                                    BlockReference reference =
                                        entity as BlockReference;
                                    if (reference != null)
                                    {
                                        Record record = Record.ForBlock(
                                            reference,
                                            SafeName(reference),
                                            SafeEffectiveName(
                                                transaction, reference),
                                            reference.Position,
                                            reference.Rotation,
                                            reference.ScaleFactors,
                                            reference.Layer,
                                            space.Name,
                                            reference.Handle.ToString());
                                        ReadAttributes(
                                            transaction,
                                            reference,
                                            record,
                                            counters);
                                        ApplyVisibility(
                                            transaction, reference, record,
                                            true, true);
                                        records.Add(record);
                                        counters.BlockReferences++;
                                        string effectiveName =
                                            record.EffectiveName;
                                        if (expandRegex != null &&
                                            (expandRegex.IsMatch(
                                                record.BlockName) ||
                                             expandRegex.IsMatch(
                                                effectiveName)) &&
                                            (expandRootHandleRegex == null ||
                                             expandRootHandleRegex.IsMatch(
                                                 reference.Handle.ToString())))
                                        {
                                            ExpandBlock(
                                                transaction,
                                                reference,
                                                space.Name,
                                                reference.Handle.ToString(),
                                                effectiveName,
                                                new List<Matrix3d>
                                                {
                                                    reference.BlockTransform
                                                },
                                                 new HashSet<ObjectId>(),
                                                 records,
                                                 counters,
                                                 maxExpandedEntities,
                                                 record.EffectiveVisible,
                                                 record.EffectivePlottable);
                                        }
                                        // Some third-party drawings expose a
                                        // visible top-level block reference
                                        // whose block table record is empty.
                                        // The definition walk above therefore
                                        // cannot recover its cached graphics.
                                        // Keep this fallback strictly
                                        // handle-directed: it explodes only an
                                        // explicitly requested source entity
                                        // in memory and never modifies the DWG.
                                        if (
                                            explodeTopLevelHandleRegex != null &&
                                            explodeTopLevelHandleRegex.IsMatch(
                                                reference.Handle.ToString()))
                                        {
                                            AppendExplodedEntities(
                                                transaction,
                                                reference,
                                                space.Name,
                                                new List<Matrix3d>(),
                                                reference.Handle.ToString(),
                                                "@TOPLEVEL[" +
                                                    reference.Handle.ToString() +
                                                    "]",
                                                records,
                                                counters,
                                                maxExpandedEntities,
                                                record.EffectiveVisible,
                                                record.EffectivePlottable,
                                                0);
                                        }
                                        continue;
                                    }
                                    Record entityRecord = Record.ForEntity(
                                        entity, space.Name);
                                    ApplyVisibility(
                                        transaction, entity, entityRecord,
                                        true, true);
                                    records.Add(entityRecord);
                                    counters.PrimitiveEntities++;
                                    if (entityRecord.Kind == "entity_bounds")
                                    {
                                        counters.DirectBoundsEntities++;
                                    }
                                    else if (
                                        entityRecord.Kind ==
                                        "entity_unresolved")
                                    {
                                        counters.DirectUnresolvedEntities++;
                                    }
                                    if (
                                        explodeTopLevelHandleRegex != null &&
                                        explodeTopLevelHandleRegex.IsMatch(
                                            entity.Handle.ToString()) &&
                                        (
                                            entityRecord.Kind ==
                                            "entity_bounds" ||
                                            entityRecord.Kind ==
                                            "entity_unresolved"))
                                    {
                                        AppendExplodedEntities(
                                            transaction,
                                            entity,
                                            space.Name,
                                            new List<Matrix3d>(),
                                            entity.Handle.ToString(),
                                            "@TOPLEVEL[" +
                                                entity.Handle.ToString() +
                                                "]",
                                            records,
                                            counters,
                                            maxExpandedEntities,
                                            entityRecord.EffectiveVisible,
                                            entityRecord.EffectivePlottable,
                                            0);
                                    }
                                }
                                catch (System.Exception)
                                {
                                    counters.Skipped++;
                                }
                            }
                            if (counters.Truncated) break;
                        }
                        transaction.Commit();
                    }
                    database.CloseInput(true);
                }
                WritePhase(
                    phasePath,
                    "scan_complete",
                    timer.Elapsed.TotalSeconds);
                File.WriteAllText(
                    outputPath,
                    ToJson(
                        input,
                        counters,
                        records,
                        timer.Elapsed.TotalSeconds,
                        expandPattern,
                        expandRootHandlePattern,
                        explodeTopLevelHandlePattern),
                    new UTF8Encoding(false));
                File.Delete(phasePath);
                if (editor != null)
                {
                    editor.WriteMessage(
                        "\nCADSIDEDBINDEXD2L: {0} top-level entities, "
                        + "{1} block references, {2} direct texts; "
                        + "{3:F3}s.\n{4}",
                        counters.TopLevelEntities,
                        counters.BlockReferences,
                        counters.DirectText,
                        timer.Elapsed.TotalSeconds,
                        outputPath);
                }
            }
            catch (System.Exception exception)
            {
                WriteFailure(
                    phasePath,
                    exception.Message,
                    timer.Elapsed.TotalSeconds);
                if (editor != null)
                {
                    editor.WriteMessage(
                        "\nCADSIDEDBINDEXD2L failed: {0}",
                        exception.Message);
                }
            }
        }

        private static void ExpandBlock(
            Transaction transaction,
            BlockReference reference,
            string space,
            string rootInstanceHandle,
            string blockPath,
            List<Matrix3d> transforms,
            HashSet<ObjectId> stack,
            List<Record> records,
            Counters counters,
            int maxExpandedEntities,
            bool ancestorVisible,
            bool ancestorPlottable)
        {
            if (counters.ExpandedEntities >= maxExpandedEntities)
            {
                counters.ExpandedTruncated = true;
                return;
            }
            ObjectId definitionId = reference.BlockTableRecord;
            if (definitionId.IsNull || !definitionId.IsValid ||
                stack.Contains(definitionId))
            {
                return;
            }
            stack.Add(definitionId);
            try
            {
                BlockTableRecord definition = transaction.GetObject(
                    definitionId,
                    OpenMode.ForRead) as BlockTableRecord;
                if (definition == null ||
                    definition.IsFromExternalReference)
                {
                    return;
                }
                foreach (ObjectId entityId in definition)
                {
                    if (counters.ExpandedEntities >= maxExpandedEntities)
                    {
                        counters.ExpandedTruncated = true;
                        break;
                    }
                    try
                    {
                        Entity entity = transaction.GetObject(
                            entityId, OpenMode.ForRead) as Entity;
                        if (entity == null) continue;
                        BlockReference nested =
                            entity as BlockReference;
                        if (nested != null)
                        {
                            bool nestedVisible =
                                IsEffectivelyVisible(
                                    transaction,
                                    nested,
                                    ancestorVisible);
                            bool nestedPlottable =
                                IsEffectivelyPlottable(
                                    transaction,
                                    nested,
                                    ancestorVisible,
                                    ancestorPlottable);
                            string nestedPath =
                                blockPath + "/" + SafeName(nested);
                            AppendExpandedAttributes(
                                transaction,
                                nested,
                                space,
                                rootInstanceHandle,
                                nestedPath,
                                transforms,
                                records,
                                counters,
                                maxExpandedEntities,
                                nestedVisible,
                                nestedPlottable);
                            var nestedTransforms =
                                new List<Matrix3d>();
                            nestedTransforms.Add(
                                nested.BlockTransform);
                            nestedTransforms.AddRange(transforms);
                            ExpandBlock(
                                transaction,
                                nested,
                                space,
                                rootInstanceHandle,
                                nestedPath,
                                nestedTransforms,
                                stack,
                                records,
                                counters,
                                maxExpandedEntities,
                                nestedVisible,
                                nestedPlottable);
                            continue;
                        }
                        Record expanded = Record.ForExpandedEntity(
                            entity,
                            space,
                            transforms,
                            rootInstanceHandle,
                            blockPath);
                        if (expanded != null)
                        {
                            if (
                                expanded.Kind == "entity_bounds" ||
                                expanded.Kind == "entity_unresolved")
                            {
                                int explodedBefore =
                                    counters.ExplodedEntities;
                                AppendExplodedEntities(
                                    transaction,
                                    entity,
                                    space,
                                    transforms,
                                    rootInstanceHandle,
                                    blockPath,
                                    records,
                                    counters,
                                    maxExpandedEntities,
                                    ancestorVisible,
                                    ancestorPlottable,
                                    0);
                                if (
                                    counters.ExplodedEntities >
                                    explodedBefore)
                                {
                                    continue;
                                }
                            }
                            ApplyVisibility(
                                transaction,
                                entity,
                                expanded,
                                ancestorVisible,
                                ancestorPlottable);
                            records.Add(expanded);
                            counters.ExpandedEntities++;
                            if (expanded.Kind == "entity_bounds")
                            {
                                counters.ExpandedBoundsEntities++;
                            }
                            else if (expanded.Kind == "entity_unresolved")
                            {
                                counters.ExpandedUnresolvedEntities++;
                            }
                        }
                    }
                    catch (System.Exception)
                    {
                        counters.Skipped++;
                    }
                }
            }
            finally
            {
                stack.Remove(definitionId);
            }
        }

        private static void AppendExplodedEntities(
            Transaction transaction,
            Entity source,
            string space,
            List<Matrix3d> transforms,
            string rootInstanceHandle,
            string blockPath,
            List<Record> records,
            Counters counters,
            int maxExpandedEntities,
            bool ancestorVisible,
            bool ancestorPlottable,
            int depth)
        {
            if (
                source == null ||
                depth >= 3 ||
                counters.ExpandedEntities >= maxExpandedEntities)
            {
                return;
            }
            var exploded = new DBObjectCollection();
            try
            {
                source.Explode(exploded);
            }
            catch (System.Exception)
            {
                counters.ExplodeFailures++;
                return;
            }
            if (exploded.Count == 0)
            {
                counters.ExplodeFailures++;
                return;
            }
            counters.ExplodedSourceEntities++;
            string sourceHandle = Record.SafeHandle(source);
            try
            {
                for (int index = 0; index < exploded.Count; index++)
                {
                    if (counters.ExpandedEntities >= maxExpandedEntities)
                    {
                        counters.ExpandedTruncated = true;
                        break;
                    }
                    Entity child = exploded[index] as Entity;
                    if (child == null) continue;
                    string childPath = blockPath + "/@EXPLODE[" +
                        sourceHandle + ":" +
                        index.ToString(CultureInfo.InvariantCulture) + "]";
                    string childHandle = sourceHandle + "#E" +
                        index.ToString(CultureInfo.InvariantCulture);
                    Record expanded = Record.ForExpandedEntity(
                        child,
                        space,
                        transforms,
                        rootInstanceHandle,
                        childPath,
                        "entity-explode",
                        childHandle);
                    if (
                        expanded.Kind == "entity_bounds" ||
                        expanded.Kind == "entity_unresolved")
                    {
                        int explodedBefore = counters.ExplodedEntities;
                        AppendExplodedEntities(
                            transaction,
                            child,
                            space,
                            transforms,
                            rootInstanceHandle,
                            childPath,
                            records,
                            counters,
                            maxExpandedEntities,
                            ancestorVisible,
                            ancestorPlottable,
                            depth + 1);
                        if (
                            counters.ExplodedEntities >
                            explodedBefore)
                        {
                            continue;
                        }
                    }
                    ApplyVisibility(
                        transaction,
                        child,
                        expanded,
                        ancestorVisible,
                        ancestorPlottable);
                    records.Add(expanded);
                    counters.ExpandedEntities++;
                    counters.ExplodedEntities++;
                    if (expanded.Kind == "entity_bounds")
                    {
                        counters.ExpandedBoundsEntities++;
                    }
                    else if (expanded.Kind == "entity_unresolved")
                    {
                        counters.ExpandedUnresolvedEntities++;
                    }
                }
            }
            finally
            {
                foreach (DBObject item in exploded)
                {
                    if (item != null) item.Dispose();
                }
            }
        }

        private static void AppendExpandedAttributes(
            Transaction transaction,
            BlockReference reference,
            string space,
            string rootInstanceHandle,
            string blockPath,
            List<Matrix3d> ancestorTransforms,
            List<Record> records,
            Counters counters,
            int maxExpandedEntities,
            bool ancestorVisible,
            bool ancestorPlottable)
        {
            foreach (ObjectId attributeId in reference.AttributeCollection)
            {
                if (counters.ExpandedEntities >= maxExpandedEntities)
                {
                    counters.ExpandedTruncated = true;
                    return;
                }
                try
                {
                    AttributeReference attribute = transaction.GetObject(
                        attributeId,
                        OpenMode.ForRead) as AttributeReference;
                    if (attribute == null ||
                        String.IsNullOrWhiteSpace(attribute.TextString))
                    {
                        continue;
                    }
                    Record expanded = Record.ForExpandedAttribute(
                        attribute,
                        space,
                        ancestorTransforms,
                        rootInstanceHandle,
                        blockPath);
                    ApplyVisibility(
                        transaction,
                        attribute,
                        expanded,
                        ancestorVisible,
                        ancestorPlottable);
                    records.Add(expanded);
                    counters.Attributes++;
                    counters.ExpandedEntities++;
                }
                catch (System.Exception)
                {
                    counters.Skipped++;
                }
            }
        }

        private static Point3d Transform(
            Point3d point,
            List<Matrix3d> transforms)
        {
            Point3d result = point;
            for (int index = 0; index < transforms.Count; index++)
            {
                result = result.TransformBy(transforms[index]);
            }
            return result;
        }

        private static double TransformRotation(
            Point3d position,
            double rotation,
            List<Matrix3d> transforms)
        {
            Point3d origin = Transform(position, transforms);
            Point3d directionPoint = Transform(
                new Point3d(
                    position.X + Math.Cos(rotation),
                    position.Y + Math.Sin(rotation),
                    position.Z),
                transforms);
            double result = Math.Atan2(
                directionPoint.Y - origin.Y,
                directionPoint.X - origin.X);
            if (result < 0.0) result += Math.PI * 2.0;
            return result;
        }

        private static void ReadAttributes(
            Transaction transaction,
            BlockReference reference,
            Record record,
            Counters counters)
        {
            foreach (ObjectId attributeId in reference.AttributeCollection)
            {
                try
                {
                    AttributeReference attribute = transaction.GetObject(
                        attributeId, OpenMode.ForRead) as AttributeReference;
                    if (attribute != null &&
                        !String.IsNullOrWhiteSpace(attribute.TextString))
                    {
                        record.Attributes.Add(new AttributeItem(
                            attribute.Tag,
                            attribute.TextString,
                            attribute.Handle.ToString()));
                        counters.Attributes++;
                    }
                }
                catch (System.Exception)
                {
                    counters.Skipped++;
                }
            }
        }

        private static void ReadVisibility(
            Transaction transaction,
            Entity entity,
            out bool entityVisible,
            out bool layerOn,
            out bool layerFrozen,
            out bool layerPlottable)
        {
            entityVisible = true;
            layerOn = true;
            layerFrozen = false;
            layerPlottable = true;
            try { entityVisible = entity.Visible; }
            catch (System.Exception) { }
            try
            {
                LayerTableRecord layer = transaction.GetObject(
                    entity.LayerId,
                    OpenMode.ForRead) as LayerTableRecord;
                if (layer != null)
                {
                    layerOn = !layer.IsOff;
                    layerFrozen = layer.IsFrozen;
                    layerPlottable = layer.IsPlottable;
                }
            }
            catch (System.Exception) { }
        }

        private static bool IsEffectivelyVisible(
            Transaction transaction,
            Entity entity,
            bool ancestorVisible)
        {
            bool entityVisible;
            bool layerOn;
            bool layerFrozen;
            bool layerPlottable;
            ReadVisibility(
                transaction,
                entity,
                out entityVisible,
                out layerOn,
                out layerFrozen,
                out layerPlottable);
            return ancestorVisible &&
                entityVisible &&
                layerOn &&
                !layerFrozen;
        }

        private static bool IsEffectivelyPlottable(
            Transaction transaction,
            Entity entity,
            bool ancestorVisible,
            bool ancestorPlottable)
        {
            bool entityVisible;
            bool layerOn;
            bool layerFrozen;
            bool layerPlottable;
            ReadVisibility(
                transaction,
                entity,
                out entityVisible,
                out layerOn,
                out layerFrozen,
                out layerPlottable);
            return ancestorVisible &&
                ancestorPlottable &&
                entityVisible &&
                layerOn &&
                !layerFrozen &&
                layerPlottable;
        }

        private static void ApplyVisibility(
            Transaction transaction,
            Entity entity,
            Record record,
            bool ancestorVisible,
            bool ancestorPlottable)
        {
            bool entityVisible;
            bool layerOn;
            bool layerFrozen;
            bool layerPlottable;
            ReadVisibility(
                transaction,
                entity,
                out entityVisible,
                out layerOn,
                out layerFrozen,
                out layerPlottable);
            record.EntityVisible = entityVisible;
            record.LayerOn = layerOn;
            record.LayerFrozen = layerFrozen;
            record.LayerPlottable = layerPlottable;
            record.EffectiveVisible =
                ancestorVisible &&
                entityVisible &&
                layerOn &&
                !layerFrozen;
            record.EffectivePlottable =
                ancestorVisible &&
                ancestorPlottable &&
                entityVisible &&
                layerOn &&
                !layerFrozen &&
                layerPlottable;
        }

        private static string SafeName(BlockReference reference)
        {
            try { return reference.Name ?? String.Empty; }
            catch (System.Exception) { return String.Empty; }
        }

        private static string SafeEffectiveName(
            Transaction transaction,
            BlockReference reference)
        {
            try
            {
                ObjectId id = reference.IsDynamicBlock
                    ? reference.DynamicBlockTableRecord
                    : reference.BlockTableRecord;
                BlockTableRecord record = transaction.GetObject(
                    id, OpenMode.ForRead) as BlockTableRecord;
                return record == null ? String.Empty : record.Name;
            }
            catch (System.Exception)
            {
                return SafeName(reference);
            }
        }

        private static void WritePhase(
            string path,
            string phase,
            double elapsedSeconds)
        {
            File.WriteAllText(
                path,
                "{\"phase\":\"" + Escape(phase)
                + "\",\"elapsed_seconds\":"
                + Number(elapsedSeconds) + "}",
                new UTF8Encoding(false));
        }

        private static void WriteFailure(
            string path,
            string message,
            double elapsedSeconds)
        {
            File.WriteAllText(
                path,
                "{\"phase\":\"failed\",\"elapsed_seconds\":"
                + Number(elapsedSeconds)
                + ",\"message\":\"" + Escape(message) + "\"}",
                new UTF8Encoding(false));
        }

        private static int ReadPositiveInteger(string value, int fallback)
        {
            int parsed;
            return Int32.TryParse(value, out parsed) && parsed > 0
                ? parsed
                : fallback;
        }

        private static string ToJson(
            string drawingPath,
            Counters counters,
            List<Record> records,
            double elapsedSeconds,
            string expandPattern,
            string expandRootHandlePattern,
            string explodeTopLevelHandlePattern)
        {
            var json = new StringBuilder();
            json.Append("{\n  \"schema_version\": \"D2L-sidedb-2.2\",");
            json.Append("\n  \"drawing\": \"")
                .Append(Escape(drawingPath)).Append("\",");
            json.Append("\n  \"scope\": \"side-database top-level "
                + "layout/model-space evidence; optional targeted recursive "
                + "block expansion; no MDI target document\",");
            json.Append("\n  \"expand_block_regex\": \"")
                .Append(Escape(expandPattern)).Append("\",");
            json.Append("\n  \"expand_root_handle_regex\": \"")
                .Append(Escape(expandRootHandlePattern)).Append("\",");
            json.Append("\n  \"explode_top_level_handle_regex\": \"")
                .Append(Escape(explodeTopLevelHandlePattern)).Append("\",");
            json.Append("\n  \"elapsed_seconds\": ")
                .Append(Number(elapsedSeconds)).Append(',');
            json.Append("\n  \"layout_space_count\": ")
                .Append(counters.LayoutSpaces).Append(',');
            json.Append("\n  \"top_level_entity_count\": ")
                .Append(counters.TopLevelEntities).Append(',');
            json.Append("\n  \"block_reference_count\": ")
                .Append(counters.BlockReferences).Append(',');
            json.Append("\n  \"direct_text_count\": ")
                .Append(counters.DirectText).Append(',');
            json.Append("\n  \"attribute_count\": ")
                .Append(counters.Attributes).Append(',');
            json.Append("\n  \"primitive_entity_count\": ")
                .Append(counters.PrimitiveEntities).Append(',');
            json.Append("\n  \"expanded_entity_count\": ")
                .Append(counters.ExpandedEntities).Append(',');
            json.Append("\n  \"expanded_bounds_entity_count\": ")
                .Append(counters.ExpandedBoundsEntities).Append(',');
            json.Append("\n  \"expanded_unresolved_entity_count\": ")
                .Append(counters.ExpandedUnresolvedEntities).Append(',');
            json.Append("\n  \"exploded_source_entity_count\": ")
                .Append(counters.ExplodedSourceEntities).Append(',');
            json.Append("\n  \"exploded_entity_count\": ")
                .Append(counters.ExplodedEntities).Append(',');
            json.Append("\n  \"explode_failure_count\": ")
                .Append(counters.ExplodeFailures).Append(',');
            json.Append("\n  \"direct_entity_bounds_count\": ")
                .Append(counters.DirectBoundsEntities).Append(',');
            json.Append("\n  \"direct_entity_unresolved_count\": ")
                .Append(counters.DirectUnresolvedEntities).Append(',');
            json.Append("\n  \"expanded_entity_bounds_are_candidate\": true,");
            json.Append("\n  \"expanded_truncated\": ")
                .Append(counters.ExpandedTruncated ? "true" : "false")
                .Append(',');
            json.Append("\n  \"skipped_object_count\": ")
                .Append(counters.Skipped).Append(',');
            json.Append("\n  \"truncated\": ")
                .Append(counters.Truncated ? "true" : "false").Append(',');
            json.Append("\n  \"absence_proven\": false,");
            json.Append("\n  \"records\": [");
            for (int index = 0; index < records.Count; index++)
            {
                if (index > 0) json.Append(',');
                Record record = records[index];
                json.Append("\n    {\"kind\": \"")
                    .Append(Escape(record.Kind))
                    .Append("\", \"entity_type\": \"")
                    .Append(Escape(record.EntityType))
                    .Append("\", \"text\": \"")
                    .Append(Escape(record.Text))
                    .Append("\", \"block_name\": \"")
                    .Append(Escape(record.BlockName))
                    .Append("\", \"effective_name\": \"")
                    .Append(Escape(record.EffectiveName))
                    .Append("\", \"origin\": \"")
                    .Append(Escape(record.Origin))
                    .Append("\", \"root_instance_handle\": \"")
                    .Append(Escape(record.RootInstanceHandle))
                    .Append("\", \"block_path\": \"")
                    .Append(Escape(record.BlockPath))
                    .Append("\", \"attribute_tag\": \"")
                    .Append(Escape(record.AttributeTag))
                    .Append("\", \"x\": ").Append(Number(record.X))
                    .Append(", \"y\": ").Append(Number(record.Y))
                    .Append(", \"z\": ").Append(Number(record.Z))
                    .Append(", \"rotation\": ")
                    .Append(Number(record.Rotation))
                    .Append(", \"rotation_space\": \"")
                    .Append(Escape(record.RotationSpace))
                    .Append("\"")
                    .Append(", \"scale_x\": ")
                    .Append(Number(record.ScaleX))
                    .Append(", \"scale_y\": ")
                    .Append(Number(record.ScaleY))
                    .Append(", \"scale_z\": ")
                    .Append(Number(record.ScaleZ))
                    .Append(", \"closed\": ")
                    .Append(record.Closed ? "true" : "false")
                    .Append(", \"radius\": ")
                    .Append(NullableNumber(record.Radius))
                    .Append(", \"start_angle\": ")
                    .Append(NullableNumber(record.StartAngle))
                    .Append(", \"end_angle\": ")
                    .Append(NullableNumber(record.EndAngle))
                    .Append(", \"measurement\": ")
                    .Append(NullableNumber(record.Measurement))
                    .Append(", \"hatch_pattern_name\": \"")
                    .Append(Escape(record.HatchPatternName))
                    .Append("\", \"hatch_pattern_type\": \"")
                    .Append(Escape(record.HatchPatternType))
                    .Append("\", \"hatch_pattern_scale\": ")
                    .Append(NullableNumber(record.HatchPatternScale))
                    .Append(", \"hatch_pattern_angle\": ")
                    .Append(NullableNumber(record.HatchPatternAngle))
                    .Append(", \"hatch_pattern_space\": ")
                    .Append(NullableNumber(record.HatchPatternSpace))
                    .Append(", \"hatch_origin\": [")
                    .Append(NullableNumber(record.HatchOriginX))
                    .Append(',')
                    .Append(NullableNumber(record.HatchOriginY))
                    .Append("], \"hatch_associative\": ")
                    .Append(NullableBoolean(record.HatchAssociative))
                    .Append(", \"hatch_area\": ")
                    .Append(NullableNumber(record.HatchArea))
                    .Append(", \"hatch_loop_count\": ")
                    .Append(record.HatchLoopCount)
                    .Append(", \"hatch_loop_error_count\": ")
                    .Append(record.HatchLoopErrorCount)
                    .Append(", \"hatch_loops_truncated\": ")
                    .Append(record.HatchLoopsTruncated ? "true" : "false")
                    .Append(", \"layer\": \"")
                    .Append(Escape(record.Layer))
                    .Append("\", \"entity_visible\": ")
                    .Append(record.EntityVisible ? "true" : "false")
                    .Append(", \"layer_on\": ")
                    .Append(record.LayerOn ? "true" : "false")
                    .Append(", \"layer_frozen\": ")
                    .Append(record.LayerFrozen ? "true" : "false")
                    .Append(", \"layer_plottable\": ")
                    .Append(record.LayerPlottable ? "true" : "false")
                    .Append(", \"effective_visible\": ")
                    .Append(record.EffectiveVisible ? "true" : "false")
                    .Append(", \"effective_plottable\": ")
                    .Append(record.EffectivePlottable ? "true" : "false")
                    .Append(", \"space\": \"")
                    .Append(Escape(record.Space))
                    .Append("\", \"handle\": \"")
                    .Append(Escape(record.Handle))
                    .Append("\", \"points\": [");
                for (int pointIndex = 0;
                    pointIndex < record.Points.Count;
                    pointIndex++)
                {
                    if (pointIndex > 0) json.Append(',');
                    Point3d point = record.Points[pointIndex];
                    json.Append('[')
                        .Append(Number(point.X)).Append(',')
                        .Append(Number(point.Y)).Append(',')
                        .Append(Number(point.Z)).Append(']');
                }
                json.Append("], \"point_roles\": [");
                for (int roleIndex = 0;
                    roleIndex < record.PointRoles.Count;
                    roleIndex++)
                {
                    if (roleIndex > 0) json.Append(',');
                    json.Append('"')
                        .Append(Escape(record.PointRoles[roleIndex]))
                        .Append('"');
                }
                json.Append("], \"hatch_loops\": [");
                for (int loopIndex = 0;
                    loopIndex < record.HatchLoops.Count;
                    loopIndex++)
                {
                    if (loopIndex > 0) json.Append(',');
                    HatchLoopEvidence loop = record.HatchLoops[loopIndex];
                    json.Append("{\"loop_index\": ")
                        .Append(loop.LoopIndex)
                        .Append(", \"loop_type\": \"")
                        .Append(Escape(loop.LoopType))
                        .Append("\", \"is_polyline\": ")
                        .Append(loop.IsPolyline ? "true" : "false")
                        .Append(", \"points\": [");
                    for (int hatchPointIndex = 0;
                        hatchPointIndex < loop.Points.Count;
                        hatchPointIndex++)
                    {
                        if (hatchPointIndex > 0) json.Append(',');
                        Point3d hatchPoint = loop.Points[hatchPointIndex];
                        json.Append('[')
                            .Append(Number(hatchPoint.X)).Append(',')
                            .Append(Number(hatchPoint.Y)).Append(',')
                            .Append(Number(hatchPoint.Z)).Append(']');
                    }
                    json.Append("], \"point_roles\": [");
                    for (int hatchRoleIndex = 0;
                        hatchRoleIndex < loop.PointRoles.Count;
                        hatchRoleIndex++)
                    {
                        if (hatchRoleIndex > 0) json.Append(',');
                        json.Append('"')
                            .Append(Escape(loop.PointRoles[hatchRoleIndex]))
                            .Append('"');
                    }
                    json.Append("], \"bulges\": [");
                    for (int bulgeIndex = 0;
                        bulgeIndex < loop.Bulges.Count;
                        bulgeIndex++)
                    {
                        if (bulgeIndex > 0) json.Append(',');
                        json.Append(Number(loop.Bulges[bulgeIndex]));
                    }
                    json.Append("], \"curve_types\": [");
                    for (int curveTypeIndex = 0;
                        curveTypeIndex < loop.CurveTypes.Count;
                        curveTypeIndex++)
                    {
                        if (curveTypeIndex > 0) json.Append(',');
                        json.Append('"')
                            .Append(Escape(loop.CurveTypes[curveTypeIndex]))
                            .Append('"');
                    }
                    json.Append("]}");
                }
                json.Append("], \"attributes\": [");
                for (int attributeIndex = 0;
                    attributeIndex < record.Attributes.Count;
                    attributeIndex++)
                {
                    if (attributeIndex > 0) json.Append(',');
                    AttributeItem attribute =
                        record.Attributes[attributeIndex];
                    json.Append("{\"tag\": \"")
                        .Append(Escape(attribute.Tag))
                        .Append("\", \"text\": \"")
                        .Append(Escape(attribute.Text))
                        .Append("\", \"handle\": \"")
                        .Append(Escape(attribute.Handle))
                        .Append("\"}");
                }
                json.Append("]}");
            }
            json.Append("\n  ]\n}\n");
            return json.ToString();
        }

        private static string Number(double value)
        {
            return value.ToString("R", CultureInfo.InvariantCulture);
        }

        private static string NullableNumber(double? value)
        {
            return value.HasValue ? Number(value.Value) : "null";
        }

        private static string NullableBoolean(bool? value)
        {
            return value.HasValue
                ? (value.Value ? "true" : "false")
                : "null";
        }

        private static string Escape(string value)
        {
            if (value == null) return String.Empty;
            return value
                .Replace("\\", "\\\\")
                .Replace("\"", "\\\"")
                .Replace("\r", "\\r")
                .Replace("\n", "\\n")
                .Replace("\t", "\\t");
        }

        private sealed class Counters
        {
            public int LayoutSpaces;
            public int TopLevelEntities;
            public int BlockReferences;
            public int DirectText;
            public int Attributes;
            public int PrimitiveEntities;
            public int ExpandedEntities;
            public int ExpandedBoundsEntities;
            public int ExpandedUnresolvedEntities;
            public int ExplodedSourceEntities;
            public int ExplodedEntities;
            public int ExplodeFailures;
            public int DirectBoundsEntities;
            public int DirectUnresolvedEntities;
            public int Skipped;
            public bool Truncated;
            public bool ExpandedTruncated;
        }

        private sealed class AttributeItem
        {
            public AttributeItem(string tag, string text, string handle)
            {
                Tag = tag ?? String.Empty;
                Text = text ?? String.Empty;
                Handle = handle ?? String.Empty;
            }
            public string Tag { get; private set; }
            public string Text { get; private set; }
            public string Handle { get; private set; }
        }

        private sealed class HatchLoopEvidence
        {
            public HatchLoopEvidence()
            {
                LoopType = String.Empty;
                Points = new List<Point3d>();
                PointRoles = new List<string>();
                Bulges = new List<double>();
                CurveTypes = new List<string>();
            }

            public int LoopIndex { get; set; }
            public string LoopType { get; set; }
            public bool IsPolyline { get; set; }
            public List<Point3d> Points { get; private set; }
            public List<string> PointRoles { get; private set; }
            public List<double> Bulges { get; private set; }
            public List<string> CurveTypes { get; private set; }
        }

        private sealed class Record
        {
            private Record()
            {
                Kind = String.Empty;
                EntityType = String.Empty;
                Text = String.Empty;
                BlockName = String.Empty;
                EffectiveName = String.Empty;
                Origin = "direct";
                RootInstanceHandle = String.Empty;
                BlockPath = String.Empty;
                AttributeTag = String.Empty;
                RotationSpace = String.Empty;
                Layer = String.Empty;
                Space = String.Empty;
                Handle = String.Empty;
                ScaleX = 1.0;
                ScaleY = 1.0;
                ScaleZ = 1.0;
                EntityVisible = true;
                LayerOn = true;
                LayerPlottable = true;
                EffectiveVisible = true;
                EffectivePlottable = true;
                HatchPatternName = String.Empty;
                HatchPatternType = String.Empty;
                Attributes = new List<AttributeItem>();
                Points = new List<Point3d>();
                PointRoles = new List<string>();
                HatchLoops = new List<HatchLoopEvidence>();
            }

            public static Record ForText(
                string type,
                string text,
                Point3d position,
                double rotation,
                string rotationSpace,
                string layer,
                string space,
                string handle)
            {
                return new Record
                {
                    Kind = "text",
                    EntityType = type,
                    Text = text ?? String.Empty,
                    X = position.X,
                    Y = position.Y,
                    Z = position.Z,
                    Rotation = rotation,
                    RotationSpace = rotationSpace ?? String.Empty,
                    Layer = layer ?? String.Empty,
                    Space = space ?? String.Empty,
                    Handle = handle ?? String.Empty
                };
            }

            public static Record ForBlock(
                BlockReference reference,
                string name,
                string effectiveName,
                Point3d position,
                double rotation,
                Scale3d scale,
                string layer,
                string space,
                string handle)
            {
                Record record = new Record
                {
                    Kind = "block_reference",
                    EntityType = "BlockReference",
                    BlockName = name ?? String.Empty,
                    EffectiveName = effectiveName ?? String.Empty,
                    X = position.X,
                    Y = position.Y,
                    Z = position.Z,
                    Rotation = rotation,
                    ScaleX = scale.X,
                    ScaleY = scale.Y,
                    ScaleZ = scale.Z,
                    Layer = layer ?? String.Empty,
                    Space = space ?? String.Empty,
                    Handle = handle ?? String.Empty
                };
                AddEntityBounds(record, reference);
                return record;
            }

            public static Record ForExpandedAttribute(
                AttributeReference attribute,
                string space,
                List<Matrix3d> ancestorTransforms,
                string rootInstanceHandle,
                string blockPath)
            {
                Record record = ForText(
                    "AttributeReference",
                    attribute.TextString,
                    Transform(attribute.Position, ancestorTransforms),
                    TransformRotation(
                        attribute.Position,
                        attribute.Rotation,
                        ancestorTransforms),
                    "expanded-world",
                    attribute.Layer,
                    space,
                    attribute.Handle.ToString());
                record.AttributeTag = attribute.Tag ?? String.Empty;
                record.Origin = "nested-block-attribute";
                record.RootInstanceHandle = rootInstanceHandle;
                record.BlockPath = blockPath;
                return record;
            }

            public static string SafeHandle(Entity entity)
            {
                if (entity == null) return String.Empty;
                try { return entity.Handle.ToString(); }
                catch (System.Exception) { return String.Empty; }
            }

            public static Record ForEntity(
                Entity entity,
                string space,
                string handleOverride = null)
            {
                var record = new Record
                {
                    Kind = "entity",
                    EntityType = entity.GetType().Name,
                    Layer = entity.Layer ?? String.Empty,
                    Space = space ?? String.Empty,
                    Handle = String.IsNullOrWhiteSpace(handleOverride)
                        ? SafeHandle(entity)
                        : handleOverride
                };
                Line line = entity as Line;
                if (line != null)
                {
                    record.Kind = "line";
                    record.Points.Add(line.StartPoint);
                    record.Points.Add(line.EndPoint);
                    record.PointRoles.Add("start");
                    record.PointRoles.Add("end");
                    return record;
                }
                Polyline polyline = entity as Polyline;
                if (polyline != null)
                {
                    record.Kind = "polyline";
                    record.Closed = polyline.Closed;
                    for (int index = 0;
                        index < polyline.NumberOfVertices;
                        index++)
                    {
                        record.Points.Add(polyline.GetPoint3dAt(index));
                        record.PointRoles.Add(
                            "vertex_" +
                            index.ToString(CultureInfo.InvariantCulture));
                    }
                    return record;
                }
                Circle circle = entity as Circle;
                if (circle != null)
                {
                    record.Kind = "circle";
                    record.Points.Add(circle.Center);
                    record.PointRoles.Add("center");
                    record.Radius = circle.Radius;
                    return record;
                }
                Arc arc = entity as Arc;
                if (arc != null)
                {
                    record.Kind = "arc";
                    record.Points.Add(arc.Center);
                    record.PointRoles.Add("center");
                    record.Radius = arc.Radius;
                    record.StartAngle = arc.StartAngle;
                    record.EndAngle = arc.EndAngle;
                    return record;
                }
                Hatch hatch = entity as Hatch;
                if (hatch != null)
                {
                    record.Kind = "hatch";
                    PopulateHatchEvidence(record, hatch);
                    AddEntityBounds(record, entity);
                    return record;
                }
                Dimension dimension = entity as Dimension;
                if (dimension != null)
                {
                    record.Kind = "dimension";
                    Point3d textPosition = dimension.TextPosition;
                    record.X = textPosition.X;
                    record.Y = textPosition.Y;
                    record.Z = textPosition.Z;
                    AddDimensionPoint(
                        record,
                        "text_position",
                        textPosition);
                    foreach (string propertyName in new string[]
                    {
                        "XLine1Point",
                        "XLine2Point",
                        "DimLinePoint",
                        "DefiningPoint",
                        "LeaderEndPoint",
                        "Center",
                        "CenterPoint",
                        "ChordPoint",
                        "FarChordPoint",
                        "XLine1Start",
                        "XLine1End",
                        "XLine2Start",
                        "XLine2End",
                        "ArcPoint"
                    })
                    {
                        AddDimensionPointByProperty(
                            record,
                            dimension,
                            propertyName);
                    }
                    try { record.Measurement = dimension.Measurement; }
                    catch (System.Exception) { }
                    try
                    {
                        record.Text = dimension.DimensionText ??
                            String.Empty;
                    }
                    catch (System.Exception) { }
                    return record;
                }
                if (!AddEntityBounds(record, entity))
                {
                    record.Kind = "entity_unresolved";
                }
                return record;
            }

            private static bool AddEntityBounds(
                Record record,
                Entity entity)
            {
                try
                {
                    Extents3d extents = entity.GeometricExtents;
                    Point3d minimum = extents.MinPoint;
                    Point3d maximum = extents.MaxPoint;
                    if (record.Kind == "entity")
                    {
                        record.Kind = "entity_bounds";
                    }
                    record.Closed = true;
                    record.Points.Add(new Point3d(
                        minimum.X, minimum.Y, minimum.Z));
                    record.Points.Add(new Point3d(
                        maximum.X, minimum.Y, minimum.Z));
                    record.Points.Add(new Point3d(
                        maximum.X, maximum.Y, maximum.Z));
                    record.Points.Add(new Point3d(
                        minimum.X, maximum.Y, maximum.Z));
                    record.PointRoles.Add("bounds_min");
                    record.PointRoles.Add("bounds_max_x_min_y");
                    record.PointRoles.Add("bounds_max");
                    record.PointRoles.Add("bounds_min_x_max_y");
                    return true;
                }
                catch (System.Exception)
                {
                    return false;
                }
            }

            private static void PopulateHatchEvidence(
                Record record,
                Hatch hatch)
            {
                const int maxLoops = 512;
                const int maxPoints = 20000;
                try
                {
                    record.HatchPatternName =
                        hatch.PatternName ?? String.Empty;
                }
                catch (System.Exception) { }
                try
                {
                    record.HatchPatternType =
                        hatch.PatternType.ToString();
                }
                catch (System.Exception) { }
                try { record.HatchPatternScale = hatch.PatternScale; }
                catch (System.Exception) { }
                try { record.HatchPatternAngle = hatch.PatternAngle; }
                catch (System.Exception) { }
                try { record.HatchPatternSpace = hatch.PatternSpace; }
                catch (System.Exception) { }
                try
                {
                    Point2d origin = hatch.Origin;
                    record.HatchOriginX = origin.X;
                    record.HatchOriginY = origin.Y;
                }
                catch (System.Exception) { }
                try { record.HatchAssociative = hatch.Associative; }
                catch (System.Exception) { }
                try { record.HatchArea = hatch.Area; }
                catch (System.Exception) { }
                int loopCount = 0;
                try { loopCount = hatch.NumberOfLoops; }
                catch (System.Exception)
                {
                    record.HatchLoopErrorCount++;
                    return;
                }
                record.HatchLoopCount = loopCount;
                int pointCount = 0;
                for (int loopIndex = 0;
                    loopIndex < loopCount;
                    loopIndex++)
                {
                    if (
                        record.HatchLoops.Count >= maxLoops ||
                        pointCount >= maxPoints)
                    {
                        record.HatchLoopsTruncated = true;
                        break;
                    }
                    try
                    {
                        HatchLoop sourceLoop = hatch.GetLoopAt(loopIndex);
                        var targetLoop = new HatchLoopEvidence
                        {
                            LoopIndex = loopIndex,
                            LoopType = sourceLoop.LoopType.ToString(),
                            IsPolyline = sourceLoop.IsPolyline
                        };
                        if (sourceLoop.IsPolyline)
                        {
                            int vertexIndex = 0;
                            foreach (BulgeVertex vertex in sourceLoop.Polyline)
                            {
                                if (pointCount >= maxPoints)
                                {
                                    record.HatchLoopsTruncated = true;
                                    break;
                                }
                                targetLoop.Points.Add(new Point3d(
                                    vertex.Vertex.X,
                                    vertex.Vertex.Y,
                                    0.0));
                                targetLoop.PointRoles.Add(
                                    "vertex_" +
                                    vertexIndex.ToString(
                                        CultureInfo.InvariantCulture));
                                targetLoop.Bulges.Add(vertex.Bulge);
                                vertexIndex++;
                                pointCount++;
                            }
                        }
                        else
                        {
                            int curveIndex = 0;
                            foreach (Curve2d curve in sourceLoop.Curves)
                            {
                                if (pointCount >= maxPoints)
                                {
                                    record.HatchLoopsTruncated = true;
                                    break;
                                }
                                targetLoop.CurveTypes.Add(
                                    curve.GetType().Name);
                                if (curve.HasStartPoint)
                                {
                                    Point2d point = curve.StartPoint;
                                    targetLoop.Points.Add(new Point3d(
                                        point.X, point.Y, 0.0));
                                    targetLoop.PointRoles.Add(
                                        "curve_" +
                                        curveIndex.ToString(
                                            CultureInfo.InvariantCulture) +
                                        "_start");
                                    pointCount++;
                                }
                                if (
                                    curve.HasEndPoint &&
                                    pointCount < maxPoints)
                                {
                                    Point2d point = curve.EndPoint;
                                    targetLoop.Points.Add(new Point3d(
                                        point.X, point.Y, 0.0));
                                    targetLoop.PointRoles.Add(
                                        "curve_" +
                                        curveIndex.ToString(
                                            CultureInfo.InvariantCulture) +
                                        "_end");
                                    pointCount++;
                                }
                                curveIndex++;
                            }
                        }
                        record.HatchLoops.Add(targetLoop);
                    }
                    catch (System.Exception)
                    {
                        record.HatchLoopErrorCount++;
                    }
                }
            }

            private static void AddDimensionPoint(
                Record record,
                string role,
                Point3d point)
            {
                record.Points.Add(point);
                record.PointRoles.Add(role ?? String.Empty);
            }

            private static void AddDimensionPointByProperty(
                Record record,
                Dimension dimension,
                string propertyName)
            {
                try
                {
                    System.Reflection.PropertyInfo property =
                        dimension.GetType().GetProperty(propertyName);
                    if (property == null ||
                        property.PropertyType != typeof(Point3d))
                    {
                        return;
                    }
                    object value = property.GetValue(dimension, null);
                    if (value is Point3d)
                    {
                        AddDimensionPoint(
                            record,
                            propertyName,
                            (Point3d)value);
                    }
                }
                catch (System.Exception)
                {
                    // Some dimension subclasses expose properties that are
                    // unavailable until their anonymous block is generated.
                }
            }

            public static Record ForExpandedEntity(
                Entity entity,
                string space,
                List<Matrix3d> transforms,
                string rootInstanceHandle,
                string blockPath,
                string origin = "block-definition",
                string handleOverride = null)
            {
                DBText text = entity as DBText;
                if (text != null)
                {
                    Record textRecord = ForText(
                        "DBText",
                        text.TextString,
                        Transform(text.Position, transforms),
                        TransformRotation(
                            text.Position,
                            text.Rotation,
                            transforms),
                        "expanded-world",
                        text.Layer,
                        space,
                        String.IsNullOrWhiteSpace(handleOverride)
                            ? SafeHandle(text)
                            : handleOverride);
                    textRecord.Origin = origin;
                    textRecord.RootInstanceHandle =
                        rootInstanceHandle;
                    textRecord.BlockPath = blockPath;
                    return textRecord;
                }
                MText mtext = entity as MText;
                if (mtext != null)
                {
                    Record textRecord = ForText(
                        "MText",
                        mtext.Contents,
                        Transform(mtext.Location, transforms),
                        TransformRotation(
                            mtext.Location,
                            mtext.Rotation,
                            transforms),
                        "expanded-world",
                        mtext.Layer,
                        space,
                        String.IsNullOrWhiteSpace(handleOverride)
                            ? SafeHandle(mtext)
                            : handleOverride);
                    textRecord.Origin = origin;
                    textRecord.RootInstanceHandle =
                        rootInstanceHandle;
                    textRecord.BlockPath = blockPath;
                    return textRecord;
                }
                Record record = ForEntity(
                    entity,
                    space,
                    handleOverride);
                if (record.Kind == "entity")
                {
                    record.Origin = origin;
                    record.RootInstanceHandle = rootInstanceHandle;
                    record.BlockPath = blockPath;
                    try
                    {
                        Extents3d extents = entity.GeometricExtents;
                        Point3d minimum = extents.MinPoint;
                        Point3d maximum = extents.MaxPoint;
                        record.Kind = "entity_bounds";
                        record.Closed = true;
                        record.Points.Add(new Point3d(
                            minimum.X, minimum.Y, minimum.Z));
                        record.Points.Add(new Point3d(
                            maximum.X, minimum.Y, minimum.Z));
                        record.Points.Add(new Point3d(
                            maximum.X, maximum.Y, maximum.Z));
                        record.Points.Add(new Point3d(
                            minimum.X, maximum.Y, maximum.Z));
                    }
                    catch (System.Exception)
                    {
                        record.Kind = "entity_unresolved";
                    }
                }
                for (int index = 0;
                    index < record.Points.Count;
                    index++)
                {
                    record.Points[index] = Transform(
                        record.Points[index], transforms);
                }
                foreach (HatchLoopEvidence loop in record.HatchLoops)
                {
                    for (int pointIndex = 0;
                        pointIndex < loop.Points.Count;
                        pointIndex++)
                    {
                        loop.Points[pointIndex] = Transform(
                            loop.Points[pointIndex], transforms);
                    }
                }
                if (record.Kind == "dimension")
                {
                    Point3d transformedTextPosition = Transform(
                        new Point3d(record.X, record.Y, record.Z),
                        transforms);
                    record.X = transformedTextPosition.X;
                    record.Y = transformedTextPosition.Y;
                    record.Z = transformedTextPosition.Z;
                }
                record.Origin = origin;
                record.RootInstanceHandle = rootInstanceHandle;
                record.BlockPath = blockPath;
                return record;
            }

            public string Kind { get; private set; }
            public string EntityType { get; private set; }
            public string Text { get; private set; }
            public string BlockName { get; private set; }
            public string EffectiveName { get; private set; }
            public string Origin { get; private set; }
            public string RootInstanceHandle { get; private set; }
            public string BlockPath { get; private set; }
            public string AttributeTag { get; private set; }
            public double X { get; private set; }
            public double Y { get; private set; }
            public double Z { get; private set; }
            public double Rotation { get; private set; }
            public string RotationSpace { get; private set; }
            public double ScaleX { get; private set; }
            public double ScaleY { get; private set; }
            public double ScaleZ { get; private set; }
            public bool Closed { get; private set; }
            public double? Radius { get; private set; }
            public double? StartAngle { get; private set; }
            public double? EndAngle { get; private set; }
            public double? Measurement { get; private set; }
            public string HatchPatternName { get; private set; }
            public string HatchPatternType { get; private set; }
            public double? HatchPatternScale { get; private set; }
            public double? HatchPatternAngle { get; private set; }
            public double? HatchPatternSpace { get; private set; }
            public double? HatchOriginX { get; private set; }
            public double? HatchOriginY { get; private set; }
            public bool? HatchAssociative { get; private set; }
            public double? HatchArea { get; private set; }
            public int HatchLoopCount { get; private set; }
            public int HatchLoopErrorCount { get; private set; }
            public bool HatchLoopsTruncated { get; private set; }
            public string Layer { get; private set; }
            public bool EntityVisible { get; set; }
            public bool LayerOn { get; set; }
            public bool LayerFrozen { get; set; }
            public bool LayerPlottable { get; set; }
            public bool EffectiveVisible { get; set; }
            public bool EffectivePlottable { get; set; }
            public string Space { get; private set; }
            public string Handle { get; private set; }
            public List<AttributeItem> Attributes { get; private set; }
            public List<Point3d> Points { get; private set; }
            public List<string> PointRoles { get; private set; }
            public List<HatchLoopEvidence> HatchLoops { get; private set; }
        }
    }
}
