import pytest
from PIL import Image

from vision_agent_tools.models.florence2 import Florence2, Florence2Config
from vision_agent_tools.shared_types import PromptTask, Florence2ModelName


def test_referring_expression_segmentation(shared_model):
    image_path = "tests/shared_data/images/cereal.jpg"
    task = PromptTask.REFERRING_EXPRESSION_SEGMENTATION
    image = Image.open(image_path)

    payload = {
        "images": [image],
        "task": task,
    }
    response = shared_model(**payload)
    assert response == [
        {
            "labels": [""],
            "polygons": [
                [
                    [
                        1.0240000486373901,
                        1.0240000486373901,
                        2046.97607421875,
                        2046.97607421875,
                        1.0240000486373901,
                        2046.97607421875,
                    ]
                ]
            ],
        }
    ]


def test_referring_expression_segmentation_ft(unzip_model):
    image_path = "tests/shared_data/images/cereal.jpg"
    task = PromptTask.REFERRING_EXPRESSION_SEGMENTATION
    model_zip_path = "tests/models/florence2/data/models/od_checkpoint.zip"
    model_path = unzip_model(model_zip_path)
    image = Image.open(image_path)

    config = Florence2Config(
        model_name=Florence2ModelName.FLORENCE_2_BASE_FT,
        fine_tuned_model_path=model_path,
    )
    small_model = Florence2(config)
    payload = {
        "images": [image],
        "task": task,
    }
    with pytest.raises(ValueError) as exc:
        small_model(**payload)
        assert (
            exc.value
            == "The task REFERRING_EXPRESSION_SEGMENTATION is not supported yet if "
            "your are using a fine-tuned model."
        )
