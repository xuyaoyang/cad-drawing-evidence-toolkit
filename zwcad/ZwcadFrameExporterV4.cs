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
    /// Read-only V4 geometry exporter. It does not assume a title-block name,
    /// paper size, or fixed label position. It exports direct closed-polyline
    /// candidates, direct line segments, and inserted-block bounds so later
    /// analysis can identify drawing frames geometrically.
    /// </summary>
    public sealed class FrameGeometryExporter
    {
        [CommandMethod("CADFRAMEEXPORT5")]
        public void ExportFrameGeometry()
        {
            Document document = Application.DocumentManager.MdiActiveDocument;
            if (document == null) return;

            Editor editor = document.Editor;
            Database database = document.Database;
            var candidates = new List<BoundsRecord>();
            var lines = new List<LineRecord>();
            var counters = new GeometryCounters();
            try
            {
                using (Transaction transaction = database.TransactionManager.StartTransaction())
                {
                    BlockTable table = (BlockTable)transaction.GetObject(database.BlockTableId, OpenMode.ForRead);
                    foreach (ObjectId blockId in table)
                    {
                        BlockTableRecord space = transaction.GetObject(blockId, OpenMode.ForRead) as BlockTableRecord;
                        if (space == null || !space.IsLayout || space.IsFromExternalReference) continue;
                        foreach (ObjectId entityId in space)
                        {
                            Entity entity = transaction.GetObject(entityId, OpenMode.ForRead) as Entity;
                            if (entity == null) continue;
                            counters.DirectEntities++;
                            Line line = entity as Line;
                            if (line != null)
                            {
                                lines.Add(new LineRecord(line, space.Name));
                                counters.LineSegments++;
                                continue;
                            }

                            Polyline polyline = entity as Polyline;
                            if (polyline != null && polyline.Closed)
                            {
                                BoundsRecord record;
                                if (TryGetBounds(polyline, "closed-polyline", "direct", space.Name, String.Empty, 0.0, out record))
                                {
                                    candidates.Add(record);
                                    counters.ClosedPolylines++;
                                }
                                continue;
                            }

                            BlockReference reference = entity as BlockReference;
                            if (reference != null)
                            {
                                BoundsRecord record;
                                if (TryGetBounds(reference, "block-reference", "direct", space.Name, reference.Name,
                                    reference.Rotation, out record))
                                {
                                    candidates.Add(record);
                                    counters.BlockReferences++;
                                }
                            }
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
                string outputPath = Path.Combine(outputDirectory, fileStem + ".cad_frame_export_v5.json");
                File.WriteAllText(outputPath, ToJson(drawingPath, counters, candidates, lines), new UTF8Encoding(false));
                editor.WriteMessage("\nCADFRAMEEXPORT5: exported {0} closed-polyline/block candidates and {1} direct line segments.\n{2}",
                    candidates.Count, lines.Count, outputPath);
            }
            catch (System.Exception exception)
            {
                editor.WriteMessage("\nCADFRAMEEXPORT5 failed: {0}", exception.Message);
            }
        }

        private static bool TryGetBounds(Entity entity, string type, string origin, string space, string blockName,
            double rotation, out BoundsRecord record)
        {
            record = null;
            try
            {
                Extents3d extents = entity.GeometricExtents;
                double width = extents.MaxPoint.X - extents.MinPoint.X;
                double height = extents.MaxPoint.Y - extents.MinPoint.Y;
                if (width <= 0.0 || height <= 0.0) return false;
                record = new BoundsRecord(type, origin, space, entity.Layer, entity.Handle.ToString(), blockName,
                    rotation, extents.MinPoint, extents.MaxPoint);
                return true;
            }
            catch (System.Exception)
            {
                return false;
            }
        }

        private static string ToJson(string drawingPath, GeometryCounters counters, List<BoundsRecord> candidates,
            List<LineRecord> lines)
        {
            StringBuilder json = new StringBuilder();
            json.Append("{\n  \"drawing\": \"").Append(Escape(drawingPath)).Append("\",");
            json.Append("\n  \"scope\": \"direct layout entities: closed polylines, block-reference extents, and line segments; no frame name or paper size assumption\",");
            json.Append("\n  \"direct_entity_count\": ").Append(counters.DirectEntities).Append(',');
            json.Append("\n  \"closed_polyline_count\": ").Append(counters.ClosedPolylines).Append(',');
            json.Append("\n  \"block_reference_count\": ").Append(counters.BlockReferences).Append(',');
            json.Append("\n  \"line_segment_count\": ").Append(counters.LineSegments).Append(',');
            json.Append("\n  \"bounds_candidates\": [");
            for (int i = 0; i < candidates.Count; i++)
            {
                if (i > 0) json.Append(',');
                AppendBounds(json, candidates[i]);
            }
            json.Append("\n  ],\n  \"line_segments\": [");
            for (int i = 0; i < lines.Count; i++)
            {
                if (i > 0) json.Append(',');
                AppendLine(json, lines[i]);
            }
            json.Append("\n  ]\n}\n");
            return json.ToString();
        }

        private static void AppendBounds(StringBuilder json, BoundsRecord record)
        {
            json.Append("\n    {\"entity_type\": \"").Append(record.Type)
                .Append("\", \"origin\": \"").Append(record.Origin)
                .Append("\", \"space\": \"").Append(Escape(record.Space))
                .Append("\", \"layer\": \"").Append(Escape(record.Layer))
                .Append("\", \"handle\": \"").Append(Escape(record.Handle))
                .Append("\", \"block_name\": \"").Append(Escape(record.BlockName))
                .Append("\", \"rotation_radians\": ").Append(Number(record.Rotation))
                .Append(", \"min_x\": ").Append(Number(record.Min.X))
                .Append(", \"min_y\": ").Append(Number(record.Min.Y))
                .Append(", \"max_x\": ").Append(Number(record.Max.X))
                .Append(", \"max_y\": ").Append(Number(record.Max.Y))
                .Append(", \"width\": ").Append(Number(record.Max.X - record.Min.X))
                .Append(", \"height\": ").Append(Number(record.Max.Y - record.Min.Y)).Append('}');
        }

        private static void AppendLine(StringBuilder json, LineRecord record)
        {
            json.Append("\n    {\"space\": \"").Append(Escape(record.Space))
                .Append("\", \"layer\": \"").Append(Escape(record.Layer))
                .Append("\", \"handle\": \"").Append(Escape(record.Handle))
                .Append("\", \"start_x\": ").Append(Number(record.Start.X))
                .Append(", \"start_y\": ").Append(Number(record.Start.Y))
                .Append(", \"end_x\": ").Append(Number(record.End.X))
                .Append(", \"end_y\": ").Append(Number(record.End.Y)).Append('}');
        }

        private static string Number(double value) { return value.ToString("R", CultureInfo.InvariantCulture); }
        private static string Escape(string value)
        {
            if (value == null) return String.Empty;
            return value.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\r", "\\r").Replace("\n", "\\n").Replace("\t", "\\t");
        }

        private sealed class GeometryCounters { public int DirectEntities; public int ClosedPolylines; public int BlockReferences; public int LineSegments; }
        private sealed class BoundsRecord
        {
            public BoundsRecord(string type, string origin, string space, string layer, string handle, string blockName,
                double rotation, Point3d min, Point3d max)
            { Type = type; Origin = origin; Space = space; Layer = layer; Handle = handle; BlockName = blockName; Rotation = rotation; Min = min; Max = max; }
            public string Type { get; private set; } public string Origin { get; private set; } public string Space { get; private set; }
            public string Layer { get; private set; } public string Handle { get; private set; } public string BlockName { get; private set; }
            public double Rotation { get; private set; } public Point3d Min { get; private set; } public Point3d Max { get; private set; }
        }
        private sealed class LineRecord
        {
            public LineRecord(Line line, string space)
            { Space = space; Layer = line.Layer; Handle = line.Handle.ToString(); Start = line.StartPoint; End = line.EndPoint; }
            public string Space { get; private set; } public string Layer { get; private set; } public string Handle { get; private set; }
            public Point3d Start { get; private set; } public Point3d End { get; private set; }
        }
    }
}
