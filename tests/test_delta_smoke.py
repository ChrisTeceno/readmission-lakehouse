def test_delta_roundtrip(spark, tmp_path):
    path = str(tmp_path / "smoke")
    rows = [("010001", "READM-30-HF-HRRP", 1.05), ("010002", "READM-30-AMI-HRRP", 0.97)]
    df = spark.createDataFrame(rows, ["facility_id", "measure_name", "excess_readmission_ratio"])

    df.write.format("delta").save(path)
    result = spark.read.format("delta").load(path)

    assert sorted(tuple(r) for r in result.collect()) == sorted(rows)
