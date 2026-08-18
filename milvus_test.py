from pymilvus import MilvusClient

client = MilvusClient("./milvus_lite.db")

schema = MilvusClient.create_schema(auto_id=False)
schema.add_field(field_name="id", datatype=client.INT64, is_primary=True)
schema.add_field(field_name="embedding", datatype=client.FLOAT_VECTOR, dim=4)

client.create_collection(
    collection_name="test_vec",
    schema=schema
)

print("Milvus‑Lite启动成功，数据保存在当前目录 milvus_lite.db")

