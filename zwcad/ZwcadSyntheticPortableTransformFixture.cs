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
    /// Creates a native ZWCAD synthetic DWG for portable-reader transform
    /// regression.  It never opens or copies an engineering drawing.
    /// </summary>
    public sealed class SyntheticPortableTransformFixture
    {
        private const string LeafName = "SYN_PORTABLE_LEAF";
        private const string ParentName = "SYN_PORTABLE_PARENT";
        private const string LayerName = "SYN-PORTABLE-REGRESSION";

        private sealed class CaseRecord
        {
            public string Name;
            public string Handle;
            public string Kind;

            public CaseRecord(string name, string handle, string kind)
            {
                Name = name;
                Handle = handle;
                Kind = kind;
            }
        }

        [CommandMethod("CADCREATESYNTHPORTABLETRANSFORM")]
        public void CreateFixture()
        {
            Document document = Application.DocumentManager.MdiActiveDocument;
            if (document == null) return;
            Editor editor = document.Editor;
            Database database = document.Database;
            string outputPath = Environment.GetEnvironmentVariable("CAD_SYNTH_PORTABLE_OUTPUT");
            if (String.IsNullOrWhiteSpace(outputPath))
            {
                editor.WriteMessage("\nCAD_SYNTH_PORTABLE_OUTPUT is not set.");
                return;
            }
            outputPath = Path.GetFullPath(outputPath);
            Directory.CreateDirectory(Path.GetDirectoryName(outputPath));
            var cases = new List<CaseRecord>();

            try
            {
                using (Transaction transaction = database.TransactionManager.StartTransaction())
                {
                    EnsureLayer(transaction, database);
                    ObjectId leafId = CreateLeaf(transaction, database);
                    ObjectId parentId = CreateParent(transaction, database, leafId, cases);
                    BlockTable table = transaction.GetObject(database.BlockTableId, OpenMode.ForRead) as BlockTable;
                    BlockTableRecord model = transaction.GetObject(
                        table[BlockTableRecord.ModelSpace], OpenMode.ForWrite) as BlockTableRecord;

                    var parent = new BlockReference(new Point3d(1000.0, 2000.0, 0.0), parentId)
                    {
                        Layer = LayerName,
                        Rotation = Degrees(-15.0),
                        ScaleFactors = new Scale3d(2.0)
                    };
                    Append(transaction, model, parent);
                    cases.Add(new CaseRecord("nested_parent_root", parent.Handle.ToString(), "BlockReference"));

                    var nonuniform = new BlockReference(new Point3d(3000.0, 1000.0, 0.0), leafId)
                    {
                        Layer = LayerName,
                        Rotation = Degrees(20.0),
                        ScaleFactors = new Scale3d(2.0, 0.5, 1.0)
                    };
                    Append(transaction, model, nonuniform);
                    AddAttribute(transaction, nonuniform, leafId, "NONUNIFORM");
                    cases.Add(new CaseRecord("nonuniform_leaf", nonuniform.Handle.ToString(), "BlockReference"));

                    var multiple = new MInsertBlock(
                        new Point3d(5000.0, 2000.0, 0.0), leafId,
                        (short)3, (short)2, 300.0, 400.0)
                    {
                        Layer = LayerName,
                        Rotation = Degrees(10.0),
                        ScaleFactors = new Scale3d(1.0)
                    };
                    Append(transaction, model, multiple);
                    AddAttribute(transaction, multiple, leafId, "MINSERT");
                    cases.Add(new CaseRecord("minsert_3x2", multiple.Handle.ToString(), "MInsertBlock"));

                    var direct = new BlockReference(new Point3d(7000.0, 500.0, 0.0), leafId)
                    {
                        Layer = LayerName,
                        Rotation = Degrees(90.0),
                        ScaleFactors = new Scale3d(1.0)
                    };
                    Append(transaction, model, direct);
                    AddAttribute(transaction, direct, leafId, "DIRECT90");
                    cases.Add(new CaseRecord("direct_rotated_leaf", direct.Handle.ToString(), "BlockReference"));
                    transaction.Commit();
                }

                database.SaveAs(outputPath, DwgVersion.Current);
                string truthPath = Path.ChangeExtension(outputPath, ".ground_truth.json");
                File.WriteAllText(truthPath, BuildGroundTruth(outputPath, cases), new UTF8Encoding(false));
                editor.WriteMessage("\nCreated synthetic portable transform fixture: {0}", outputPath);
            }
            catch (System.Exception exception)
            {
                try { File.WriteAllText(outputPath + ".error.txt", exception.ToString(), new UTF8Encoding(false)); }
                catch { }
                editor.WriteMessage("\nCADCREATESYNTHPORTABLETRANSFORM failed: {0}\n{1}", exception.Message, exception.StackTrace);
            }
        }

        private static ObjectId CreateLeaf(Transaction transaction, Database database)
        {
            BlockTable table = transaction.GetObject(database.BlockTableId, OpenMode.ForRead) as BlockTable;
            if (table.Has(LeafName)) return table[LeafName];
            table.UpgradeOpen();
            var definition = new BlockTableRecord { Name = LeafName };
            ObjectId id = table.Add(definition);
            transaction.AddNewlyCreatedDBObject(definition, true);

            Append(transaction, definition, new Line(new Point3d(0, 0, 0), new Point3d(100, 0, 0)) { Layer = LayerName });
            var polyline = new Polyline { Layer = LayerName, Closed = true };
            polyline.AddVertexAt(0, new Point2d(0, 20), 0.0, 0.0, 0.0);
            polyline.AddVertexAt(1, new Point2d(100, 20), 0.0, 0.0, 0.0);
            polyline.AddVertexAt(2, new Point2d(100, 60), 0.0, 0.0, 0.0);
            polyline.AddVertexAt(3, new Point2d(0, 60), 0.0, 0.0, 0.0);
            Append(transaction, definition, polyline);
            Append(transaction, definition, new Circle(new Point3d(50, 100, 0), Vector3d.ZAxis, 20) { Layer = LayerName });
            Append(transaction, definition, new Arc(new Point3d(50, 100, 0), 30, 0, Math.PI / 2.0) { Layer = LayerName });
            Append(transaction, definition, new DBPoint(new Point3d(25, 100, 0)) { Layer = LayerName });
            Append(transaction, definition, new DBText
            {
                TextString = "LEAF_TEXT",
                Position = new Point3d(10, 140, 0),
                Height = 12,
                Layer = LayerName
            });
            Append(transaction, definition, new AttributeDefinition
            {
                Tag = "CODE",
                Prompt = "CODE",
                TextString = "DEFAULT",
                Position = new Point3d(10, 165, 0),
                Height = 12,
                Layer = LayerName
            });
            return id;
        }

        private static ObjectId CreateParent(
            Transaction transaction,
            Database database,
            ObjectId leafId,
            List<CaseRecord> cases)
        {
            BlockTable table = transaction.GetObject(database.BlockTableId, OpenMode.ForRead) as BlockTable;
            if (table.Has(ParentName)) return table[ParentName];
            table.UpgradeOpen();
            var definition = new BlockTableRecord { Name = ParentName };
            ObjectId id = table.Add(definition);
            transaction.AddNewlyCreatedDBObject(definition, true);
            var nested = new BlockReference(new Point3d(200.0, 100.0, 0.0), leafId)
            {
                Layer = LayerName,
                Rotation = Degrees(30.0),
                ScaleFactors = new Scale3d(1.5)
            };
            Append(transaction, definition, nested);
            AddAttribute(transaction, nested, leafId, "NESTED");
            cases.Add(new CaseRecord("nested_leaf_definition", nested.Handle.ToString(), "BlockReference"));
            Append(transaction, definition, new Line(new Point3d(0, 0, 0), new Point3d(300, 0, 0)) { Layer = LayerName });
            return id;
        }

        private static void AddAttribute(Transaction transaction, BlockReference reference, ObjectId definitionId, string value)
        {
            BlockTableRecord definition = transaction.GetObject(definitionId, OpenMode.ForRead) as BlockTableRecord;
            foreach (ObjectId entityId in definition)
            {
                AttributeDefinition template = transaction.GetObject(entityId, OpenMode.ForRead) as AttributeDefinition;
                if (template == null || template.Constant) continue;
                var attribute = new AttributeReference();
                attribute.SetAttributeFromBlock(template, reference.BlockTransform);
                attribute.TextString = value;
                reference.AttributeCollection.AppendAttribute(attribute);
                transaction.AddNewlyCreatedDBObject(attribute, true);
            }
        }

        private static void EnsureLayer(Transaction transaction, Database database)
        {
            LayerTable table = transaction.GetObject(database.LayerTableId, OpenMode.ForRead) as LayerTable;
            if (table.Has(LayerName)) return;
            table.UpgradeOpen();
            var layer = new LayerTableRecord { Name = LayerName };
            table.Add(layer);
            transaction.AddNewlyCreatedDBObject(layer, true);
        }

        private static void Append(Transaction transaction, BlockTableRecord owner, Entity entity)
        {
            owner.AppendEntity(entity);
            transaction.AddNewlyCreatedDBObject(entity, true);
        }

        private static double Degrees(double value) { return value * Math.PI / 180.0; }

        private static string BuildGroundTruth(string path, List<CaseRecord> cases)
        {
            var builder = new StringBuilder();
            builder.Append("{\n");
            builder.Append("  \"fixture\": \"synthetic_portable_transform\",\n");
            builder.Append("  \"synthetic\": true,\n");
            builder.Append("  \"not_engineering_evidence\": true,\n");
            builder.AppendFormat(CultureInfo.InvariantCulture, "  \"drawing\": \"{0}\",\n", Escape(path));
            builder.Append("  \"expected\": {\n");
            builder.Append("    \"nested_rotation_degrees\": 30.0,\n");
            builder.Append("    \"root_rotation_degrees\": -15.0,\n");
            builder.Append("    \"nonuniform_scale\": [2.0, 0.5, 1.0],\n");
            builder.Append("    \"minsert_columns\": 3,\n");
            builder.Append("    \"minsert_rows\": 2,\n");
            builder.Append("    \"minsert_column_spacing\": 300.0,\n");
            builder.Append("    \"minsert_row_spacing\": 400.0\n");
            builder.Append("  },\n  \"cases\": [\n");
            for (int index = 0; index < cases.Count; index++)
            {
                CaseRecord item = cases[index];
                builder.AppendFormat(
                    CultureInfo.InvariantCulture,
                    "    {{\"name\":\"{0}\",\"handle\":\"{1}\",\"kind\":\"{2}\"}}{3}\n",
                    Escape(item.Name), Escape(item.Handle), Escape(item.Kind), index + 1 == cases.Count ? "" : ",");
            }
            builder.Append("  ]\n}\n");
            return builder.ToString();
        }

        private static string Escape(string value)
        {
            return (value ?? String.Empty).Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\r", "\\r").Replace("\n", "\\n");
        }
    }
}
