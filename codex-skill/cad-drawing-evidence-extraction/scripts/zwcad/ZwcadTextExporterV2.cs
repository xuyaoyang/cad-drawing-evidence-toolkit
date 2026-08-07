using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Text;
using ZwSoft.ZwCAD.ApplicationServices;
using ZwSoft.ZwCAD.DatabaseServices;
using ZwSoft.ZwCAD.EditorInput;
using ZwSoft.ZwCAD.Geometry;
using ZwSoft.ZwCAD.Runtime;

namespace CadReadingExploration
{
    /// <summary>
    /// Version 2: export direct text, block attributes, and ordinary text inside
    /// nested block definitions. Points are transformed from block-local to WCS.
    /// Proxy entities remain outside this proof of concept.
    /// </summary>
    public sealed class RecursiveTextExporter
    {
#if SAFE_COMMAND
        [CommandMethod("CADTEXTEXPORT5SAFE")]
#else
        [CommandMethod("CADTEXTEXPORT5")]
#endif
        public void ExportText()
        {
            Document document = Application.DocumentManager.MdiActiveDocument;
            if (document == null) return;

            Editor editor = document.Editor;
            Database database = document.Database;
            var records = new List<TextRecord>();
            var counters = new ExportCounters();

            try
            {
                using (Transaction transaction = database.TransactionManager.StartTransaction())
                {
                    BlockTable table = (BlockTable)transaction.GetObject(database.BlockTableId, OpenMode.ForRead);
                    foreach (ObjectId blockId in table)
                    {
                        try
                        {
                            BlockTableRecord space = transaction.GetObject(blockId, OpenMode.ForRead) as BlockTableRecord;
                            if (space == null || !space.IsLayout || space.IsFromExternalReference) continue;

                            foreach (ObjectId entityId in space)
                            {
                                try
                                {
                                    Entity entity = transaction.GetObject(entityId, OpenMode.ForRead) as Entity;
                                    if (entity == null) continue;

                                    DBText text = entity as DBText;
                                    if (text != null)
                                    {
                                        Add(records, "DBText", "direct", text.TextString, text.Position, text.Layer,
                                            space.Name, text.Handle.ToString(), String.Empty);
                                        continue;
                                    }

                                    MText mtext = entity as MText;
                                    if (mtext != null)
                                    {
                                        Add(records, "MText", "direct", mtext.Contents, mtext.Location, mtext.Layer,
                                            space.Name, mtext.Handle.ToString(), String.Empty);
                                        continue;
                                    }

                                    BlockReference reference = entity as BlockReference;
                                    if (reference == null) continue;
                                    counters.BlockReferences++;
                                    ReadAttributes(transaction, records, reference, space.Name, counters);
                                    ReadBlockDefinition(transaction, records, reference, space.Name,
                                        new List<Matrix3d> { reference.BlockTransform }, new HashSet<ObjectId>(),
                                        reference.Name, counters);
                                }
                                catch (System.Exception)
                                {
                                    counters.SkippedObjectErrors++;
                                }
                            }
                        }
                        catch (System.Exception)
                        {
                            counters.SkippedObjectErrors++;
                        }
                    }
                    transaction.Commit();
                }

                string drawingPath = database.Filename;
                string directory = String.IsNullOrWhiteSpace(drawingPath)
                    ? Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory)
                    : Path.GetDirectoryName(drawingPath);
                string outputDirectory = Path.GetFullPath(Path.Combine(directory, "..", "输出"));
                Directory.CreateDirectory(outputDirectory);
                string fileStem = Path.GetFileNameWithoutExtension(drawingPath);
                string outputPath = Path.Combine(outputDirectory, fileStem + ".cad_text_export_v5.json");
                File.WriteAllText(outputPath, ToJson(drawingPath, counters, records), new UTF8Encoding(false));
                editor.WriteMessage("\nCADTEXTEXPORT5: exported {0} text records; {1} came from block definitions; {2} block references scanned; {3} invalid/special objects skipped.\n{4}",
                    records.Count, counters.BlockDefinitionText, counters.BlockReferences,
                    counters.SkippedObjectErrors, outputPath);
            }
            catch (System.Exception exception)
            {
                editor.WriteMessage("\nCADTEXTEXPORT5 failed: {0}", exception.Message);
            }
        }

        private static void ReadAttributes(Transaction transaction, List<TextRecord> records,
            BlockReference reference, string space, ExportCounters counters)
        {
            foreach (ObjectId attributeId in reference.AttributeCollection)
            {
                try
                {
                    AttributeReference attribute = transaction.GetObject(attributeId, OpenMode.ForRead) as AttributeReference;
                    if (attribute != null)
                    {
                        Add(records, "AttributeReference", "block-attribute", attribute.TextString, attribute.Position,
                            attribute.Layer, space, attribute.Handle.ToString(), reference.Name);
                    }
                }
                catch (System.Exception)
                {
                    counters.SkippedObjectErrors++;
                }
            }
        }

        private static void ReadBlockDefinition(Transaction transaction, List<TextRecord> records,
            BlockReference reference, string space, List<Matrix3d> transforms, HashSet<ObjectId> stack,
            string path, ExportCounters counters)
        {
            ObjectId definitionId = reference.BlockTableRecord;
            if (definitionId.IsNull || !definitionId.IsValid)
            {
                counters.SkippedObjectErrors++;
                return;
            }
            if (stack.Contains(definitionId)) return;
            stack.Add(definitionId);
            try
            {
                BlockTableRecord definition;
                try
                {
                    definition = transaction.GetObject(definitionId, OpenMode.ForRead) as BlockTableRecord;
                }
                catch (System.Exception)
                {
                    counters.SkippedObjectErrors++;
                    return;
                }
                if (definition == null || definition.IsFromExternalReference) return;

                foreach (ObjectId entityId in definition)
                {
                    try
                    {
                        Entity entity = transaction.GetObject(entityId, OpenMode.ForRead) as Entity;
                        if (entity == null) continue;
                        DBText text = entity as DBText;
                        if (text != null)
                        {
                            if (Add(records, "DBText", "block-definition", text.TextString, Transform(text.Position, transforms),
                                text.Layer, space, text.Handle.ToString(), path)) counters.BlockDefinitionText++;
                            continue;
                        }
                        MText mtext = entity as MText;
                        if (mtext != null)
                        {
                            if (Add(records, "MText", "block-definition", mtext.Contents, Transform(mtext.Location, transforms),
                                mtext.Layer, space, mtext.Handle.ToString(), path)) counters.BlockDefinitionText++;
                            continue;
                        }
                        BlockReference nested = entity as BlockReference;
                        if (nested == null) continue;
                        counters.NestedBlockReferences++;
                        var nestedTransforms = new List<Matrix3d>();
                        nestedTransforms.Add(nested.BlockTransform);
                        nestedTransforms.AddRange(transforms);
                        ReadAttributes(transaction, records, nested, space, counters);
                        ReadBlockDefinition(transaction, records, nested, space, nestedTransforms, stack,
                            path + "/" + nested.Name, counters);
                    }
                    catch (System.Exception)
                    {
                        counters.SkippedObjectErrors++;
                    }
                }
            }
            finally
            {
                stack.Remove(definitionId);
            }
        }

        private static Point3d Transform(Point3d point, List<Matrix3d> transforms)
        {
            Point3d transformed = point;
            for (int i = 0; i < transforms.Count; i++) transformed = transformed.TransformBy(transforms[i]);
            return transformed;
        }

        private static bool Add(List<TextRecord> records, string type, string origin, string text, Point3d position,
            string layer, string space, string handle, string blockPath)
        {
            if (!String.IsNullOrWhiteSpace(text))
            {
                records.Add(new TextRecord(type, origin, text, position, layer, space, handle, blockPath));
                return true;
            }
            return false;
        }

        private static string ToJson(string drawingPath, ExportCounters counters, List<TextRecord> records)
        {
            StringBuilder json = new StringBuilder();
            json.Append("{\n  \"drawing\": \"").Append(Escape(drawingPath)).Append("\",");
            json.Append("\n  \"scope\": \"model/paper-space text, block attributes, and recursively expanded standard text in block definitions\",");
            json.Append("\n  \"block_reference_count\": ").Append(counters.BlockReferences).Append(',');
            json.Append("\n  \"nested_block_reference_count\": ").Append(counters.NestedBlockReferences).Append(',');
            json.Append("\n  \"block_definition_text_count\": ").Append(counters.BlockDefinitionText).Append(',');
            json.Append("\n  \"skipped_object_error_count\": ").Append(counters.SkippedObjectErrors).Append(',');
            json.Append("\n  \"text_record_count\": ").Append(records.Count).Append(',');
            json.Append("\n  \"records\": [");
            for (int i = 0; i < records.Count; i++)
            {
                TextRecord r = records[i]; if (i > 0) json.Append(',');
                json.Append("\n    {\"entity_type\": \"").Append(r.Type).Append("\", \"origin\": \"").Append(r.Origin)
                    .Append("\", \"text\": \"").Append(Escape(r.Text)).Append("\", \"x\": ").Append(Number(r.Position.X))
                    .Append(", \"y\": ").Append(Number(r.Position.Y)).Append(", \"z\": ").Append(Number(r.Position.Z))
                    .Append(", \"layer\": \"").Append(Escape(r.Layer)).Append("\", \"space\": \"").Append(Escape(r.Space))
                    .Append("\", \"handle\": \"").Append(Escape(r.Handle)).Append("\", \"block_path\": \"")
                    .Append(Escape(r.BlockPath)).Append("\"}");
            }
            json.Append("\n  ]\n}\n"); return json.ToString();
        }

        private static string Number(double value) { return value.ToString("R", CultureInfo.InvariantCulture); }
        private static string Escape(string value)
        {
            if (value == null) return String.Empty;
            return value.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\r", "\\r").Replace("\n", "\\n").Replace("\t", "\\t");
        }

        private sealed class ExportCounters
        {
            public int BlockReferences;
            public int NestedBlockReferences;
            public int BlockDefinitionText;
            public int SkippedObjectErrors;
        }
        private sealed class TextRecord
        {
            public TextRecord(string type, string origin, string text, Point3d position, string layer, string space, string handle, string blockPath)
            { Type = type; Origin = origin; Text = text; Position = position; Layer = layer; Space = space; Handle = handle; BlockPath = blockPath; }
            public string Type { get; private set; } public string Origin { get; private set; } public string Text { get; private set; }
            public Point3d Position { get; private set; } public string Layer { get; private set; } public string Space { get; private set; }
            public string Handle { get; private set; } public string BlockPath { get; private set; }
        }
    }
}
