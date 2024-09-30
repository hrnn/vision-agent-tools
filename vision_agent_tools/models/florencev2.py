from enum import Enum
from typing import Annotated, Any, List

import torch
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field, validate_arguments, validate_call
from transformers import AutoModelForCausalLM, AutoProcessor

from vision_agent_tools.shared_types import (
    BaseMLModel,
    Device,
    VideoNumpy,
    BboxLabel,
)
from vision_agent_tools.models.utils import (
    calculate_bbox_iou,
    convert_florence_bboxes_to_bbox_labels,
    convert_bbox_labels_to_florence_bboxes,
)


MODEL_NAME = "microsoft/Florence-2-large"
PROCESSOR_NAME = "microsoft/Florence-2-large"


class PromptTask(str, Enum):
    """
    Valid task_prompts options for the Florence2 model.

    """

    CAPTION = "<CAPTION>"
    """"""
    CAPTION_TO_PHRASE_GROUNDING = "<CAPTION_TO_PHRASE_GROUNDING>"
    """"""
    DETAILED_CAPTION = "<DETAILED_CAPTION>"
    """"""
    MORE_DETAILED_CAPTION = "<MORE_DETAILED_CAPTION>"
    """"""
    DENSE_REGION_CAPTION = "<DENSE_REGION_CAPTION>"
    """"""
    OPEN_VOCABULARY_DETECTION = "<OPEN_VOCABULARY_DETECTION>"
    """"""
    OBJECT_DETECTION = "<OD>"
    """"""
    OCR = "<OCR>"
    """"""
    OCR_WITH_REGION = "<OCR_WITH_REGION>"
    """"""
    REGION_PROPOSAL = "<REGION_PROPOSAL>"
    """"""
    REFERRING_EXPRESSION_SEGMENTATION = "<REFERRING_EXPRESSION_SEGMENTATION>"
    """"""
    REGION_TO_SEGMENTATION = "<REGION_TO_SEGMENTATION>"
    """"""
    REGION_TO_CATEGORY = "<REGION_TO_CATEGORY>"
    """"""
    REGION_TO_DESCRIPTION = "<REGION_TO_DESCRIPTION>"
    """"""


class Florencev2(BaseMLModel):
    """
    [Florence-2](https://huggingface.co/microsoft/Florence-2-base) can interpret simple
    text prompts to perform tasks like captioning, object detection, and segmentation.

    NOTE: The Florence-2 model can only be used in GPU environments.
    """

    config = ConfigDict(arbitrary_types_allowed=True)

    def _process_image(self, image: Image.Image) -> Image.Image:
        return image.convert("RGB")

    def _process_video(self, images: VideoNumpy) -> list[Image.Image]:
        return [self._process_image(Image.fromarray(arr)) for arr in images]

    def _dummy_agnostic_nms(
        self, predictions: list[BboxLabel], nms_threshold: float
    ) -> list[BboxLabel]:
        """
        Applies a dummy agnostic Non-Maximum Suppression (NMS) to filter overlapping predictions.

        Parameters:
            predictions (list[BboxLabel]): Dictionary of predictions.
            nms_threshold (float): The IoU threshold value used for NMS.

        Returns:
            list[BboxLabel]: Filtered predictions after applying NMS.
        """
        filtered_predictions: list[BboxLabel] = []
        prediction_items = predictions

        while prediction_items:
            best_prediction = prediction_items.pop(0)
            filtered_predictions.append(best_prediction)

            prediction_items = [
                pred
                for pred in prediction_items
                if calculate_bbox_iou(best_prediction.bbox, pred.bbox) < nms_threshold
            ]

        return filtered_predictions

    def __init__(self, device: Device | None = Device.GPU) -> None:
        """
        Initializes the Florence-2 model.
        """
        self._model = AutoModelForCausalLM.from_pretrained(
            MODEL_NAME, trust_remote_code=True
        )
        self._processor = AutoProcessor.from_pretrained(
            PROCESSOR_NAME, trust_remote_code=True
        )

        if device is None:
            self.device = (
                "cuda"
                if torch.cuda.is_available()
                else "mps"
                if torch.backends.mps.is_available()
                else "cpu"
            )
        else:
            self.device = device.value
        self._model.to(self.device)
        self._model.eval()

    @validate_call(config={"arbitrary_types_allowed": True})
    @torch.inference_mode()
    @validate_arguments(config=config)
    def __call__(
        self,
        task: PromptTask,
        image: Image.Image | None = None,
        images: List[Image.Image] | None = None,
        video: VideoNumpy | None = None,
        prompt: str | None = "",
        batch_size: Annotated[int, Field(ge=1, le=10)] = 5,
        nms_threshold: Annotated[float, Field(ge=0.1, le=1.0)] = 1.0,
    ) -> Any:
        """
        Performs inference on the Florence-2 model based on the provided task, images, video (optional), and prompt.

        Florence-2 is a sequence-to-sequence architecture excelling in both zero-shot and fine-tuned settings, making it a competitive vision foundation model.

        For more examples and details, refer to the [Florence-2 sample usage](https://huggingface.co/microsoft/Florence-2-large/blob/main/sample_inference.ipynb).

        Args:
            task (PromptTask): The specific task to be performed.
            image (Image.Image): A single image for the model to process. None if using video or a list of images.
            images (List[Image.Image]): A list of images for the model to process. None if using video or a single image
            video (VideoNumpy): A NumPy representation of the video for inference. None if using images.
            prompt (str): An optional text prompt to complement the task.
            batch_size (int): The batch size used for processing multiple images or video frames.
            nms_threshold (float): The IoU threshold value used to apply a dummy agnostic Non-Maximum Suppression (NMS).

        Returns:
            Any: The output of the Florence-2 model based on the provided task, images/video, and prompt. The output type can vary depending on the chosen task.
        """

        if isinstance(task, str):
            try:
                task = PromptTask(task)
            except ValueError:
                raise ValueError(f"Invalid task string: {task}")

        if prompt is None:
            prompt = ""
        elif not isinstance(prompt, str):
            raise ValueError("prompt must be a string or None.")

        # Validate input parameters
        if image is None and images is None and video is None:
            raise ValueError("Either 'image', 'images', or 'video' must be provided.")

        # Ensure only one of image, images, or video is provided
        if (image is not None and (images is not None or video is not None)) or (
            images is not None and video is not None
        ):
            raise ValueError(
                "Only one of 'image', 'images', or 'video' can be provided."
            )

        if image is not None:
            # Single image processing
            text_input = str(task.value) + prompt
            image = self._process_image(image)
            results = self._batch_image_call([text_input], [image], task, nms_threshold)
            return results[0]
        elif images is not None:
            # Batch processing
            images_list = [self._process_image(img) for img in images]
            num_images = len(images_list)

            # Create text_inputs by repeating the task and prompt for each image
            text_input = str(task.value) + prompt
            text_inputs = [text_input] * num_images

            results = []

            # Split images and text_inputs into batches
            for i in range(0, num_images, batch_size):
                batch_images = images_list[i : i + batch_size]
                batch_text_inputs = text_inputs[i : i + batch_size]
                batch_results = self._batch_image_call(
                    batch_text_inputs, batch_images, task
                )
                results.extend(batch_results)

            return results

        elif video is not None:
            # Process video frames
            images_list = self._process_video(video)
            num_images = len(images_list)

            # Create text_inputs by repeating the task and prompt for each frame
            text_input = str(task.value) + prompt
            text_inputs = [text_input] * num_images

            results = []

            # Split frames and text_inputs into batches
            for i in range(0, num_images, batch_size):
                batch_images = images_list[i : i + batch_size]
                batch_text_inputs = text_inputs[i : i + batch_size]
                batch_results = self._batch_image_call(
                    batch_text_inputs, batch_images, task
                )
                results.extend(batch_results)

            return results

    def _batch_image_call(
        self,
        text_inputs: List[str],
        images: List[Image.Image],
        task: PromptTask,
        nms_threshold: float = 1.0,
    ):
        inputs = self._processor(
            text=text_inputs,
            images=images,
            return_tensors="pt",
        ).to(self.device)

        with torch.autocast(device_type=self.device):
            generated_ids = self._model.generate(
                input_ids=inputs["input_ids"],
                pixel_values=inputs["pixel_values"],
                max_new_tokens=1024,
                num_beams=3,
                early_stopping=False,
                do_sample=False,
            )

        # Set skip_special_tokens based on the task
        if task == PromptTask.OCR:
            skip_special_tokens = True
        else:
            skip_special_tokens = False

        generated_texts = self._processor.batch_decode(
            generated_ids, skip_special_tokens=skip_special_tokens
        )

        results = []
        for text, img in zip(generated_texts, images):
            parsed_answer = self._processor.post_process_generation(
                text, task=task, image_size=(img.width, img.height)
            )
            if (
                task == PromptTask.OBJECT_DETECTION
                or task == PromptTask.CAPTION_TO_PHRASE_GROUNDING
            ):
                preds = convert_florence_bboxes_to_bbox_labels(parsed_answer[task])
                # Run a dummy NMS to get rid of any overlapping predictions on the same object
                filtered_preds = self._dummy_agnostic_nms(preds, nms_threshold)
                # format the output to match the original format and update the output predictions
                parsed_answer[task] = convert_bbox_labels_to_florence_bboxes(
                    filtered_preds
                )
            results.append(parsed_answer)
        return results

    def to(self, device: Device):
        self._model.to(device=device.value)

    def predict(
        self, images: list[Image.Image], prompts: List[str] | None = None, **kwargs
    ) -> Any:
        task = kwargs.get("task", "")
        results = self.__call__(task=task, images=images, prompt=prompts)
        return results


class FlorenceV2ODRes(BaseModel):
    """
    Schema for the <OD> task.
    """

    bboxes: List[List[float]] = Field(
        ..., description="List of bounding boxes, each represented as [x1, y1, x2, y2]"
    )
    labels: List[str] = Field(
        ..., description="List of labels corresponding to each bounding box"
    )

    class Config:
        schema_extra = {
            "example": {
                "<OD>": {
                    "bboxes": [
                        [
                            33.599998474121094,
                            159.59999084472656,
                            596.7999877929688,
                            371.7599792480469,
                        ],
                        [
                            454.0799865722656,
                            96.23999786376953,
                            580.7999877929688,
                            261.8399963378906,
                        ],
                        [
                            224.95999145507812,
                            86.15999603271484,
                            333.7599792480469,
                            164.39999389648438,
                        ],
                        [
                            449.5999755859375,
                            276.239990234375,
                            554.5599975585938,
                            370.3199768066406,
                        ],
                        [
                            91.19999694824219,
                            280.0799865722656,
                            198.0800018310547,
                            370.3199768066406,
                        ],
                    ],
                    "labels": ["car", "door", "door", "wheel", "wheel"],
                }
            }
        }
